"""CLI for semantic search over markdown vaults."""

import json as _json
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import tyro
from tyro.conf import Positional

from memex_md.config import CONFIG_PATH, Config, VaultConfig, db_path_for_vault, load_config, save_config
from memex_md.db import (
    IndexedNote,
    find_links_to_note,
    get_all_findable,
    get_backlinks,
    get_connection,
    get_files_with_path,
    get_files_with_title,
    get_note,
    get_note_embedding,
    get_outlinks,
    search_semantic,
)
from memex_md.embeddings import embed_text, get_embedding_dim
from memex_md.find import find_notes
from memex_md.indexer import index_vault
from memex_md.logging import LOG_FILE, get_logger

log = get_logger()


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in ("--version", "-V"):
        from importlib.metadata import version

        print(version("memex-md"))
        return

    tyro.extras.subcommand_cli_from_dict(
        {
            "find": find,
            "search": search,
            "explore": explore,
            "rename": rename,
            "index": index,
            "vault:add": vault_add,
            "vault:list": vault_list,
            "vault:info": vault_info,
            "vault:remove": vault_remove,
            "logs": logs,
            "config": config,
        },
        prog="memex",
        description="Semantic search CLI for markdown vaults.",
    )


# ── CLI subcommands ──────────────────────────────────────────────────────────


def find(
    query: Positional[str],
    vault: Annotated[str | None, tyro.conf.arg(aliases=["-v"])] = None,
    limit: Annotated[int, tyro.conf.arg(aliases=["-n"])] = 10,
) -> None:
    """Find notes by name, alias, or path using fuzzy matching.

    Matches against filenames, frontmatter aliases, and paths.
    Ranked: exact > substring > fuzzy. No embeddings needed — instant.

    Examples:
        memex find knn
        memex find "neural ordinary"
        memex find auth -v work
    """
    result = do_find(query=query, vault=vault, limit=limit)
    _output(result, formatter=_format_find)


def search(
    query: Positional[str],
    vault: Annotated[str | None, tyro.conf.arg(aliases=["-v"])] = None,
    limit: Annotated[int, tyro.conf.arg(aliases=["-n"])] = 5,
    page: Annotated[int, tyro.conf.arg(aliases=["-p"])] = 1,
    full: Annotated[bool, tyro.conf.arg(aliases=["-f"])] = False,
) -> None:
    """Search notes using embedding similarity.

    Embeds the query and ranks all indexed notes by cosine distance.
    Searches all configured vaults unless -v is given. Use natural
    language queries (questions work well). Returns paths by default;
    --full includes note content.

    Examples:
        memex search "How does the auth flow handle token refresh?"
        memex search "What tradeoffs did we consider for caching?" -v work
        memex search "deployment architecture" -n 10 --full
    """
    result = do_search(query=query, vault=vault, limit=limit, page=page, concise=not full)
    _output(result, formatter=_format_search)


def explore(
    note_path: Positional[str],
    vault: Positional[str],
    full: Annotated[bool, tyro.conf.arg(aliases=["-f"])] = False,
) -> None:
    """Show a note's outlinks, backlinks, and similar notes.

    Outlinks: notes referenced via [[wikilinks]].
    Backlinks: notes that link to this note.
    Similar: nearest neighbors by embedding distance (excludes linked notes).

    note_path can be a title ("auth") or path ("docs/auth.md"). Titles must
    be unique in the vault; use path if ambiguous. --full includes note
    content and metadata.

    Examples:
        memex explore auth personal
        memex explore docs/api-design work --full
    """
    result = do_explore(note_path=note_path, vault=vault, concise=not full)
    _output(result, formatter=_format_explore)


def rename(
    note_path: Positional[str],
    new_name: Positional[str],
    vault: Positional[str],
) -> None:
    """Rename a note file and update all [[wikilinks]] pointing to it.

    Handles path links ([[subdir/note]]), title links ([[note]]),
    aliases ([[note|Display]]), heading refs ([[note#section]]).
    Ambiguous links (multiple files share a name) are skipped with warning.

    Examples:
        memex rename old-name new-name personal
        memex rename docs/guide manual work
    """
    result = do_rename(note_path=note_path, new_name=new_name, vault=vault)
    _output(result, formatter=_format_rename)


def index(
    vault: Annotated[str | None, tyro.conf.arg(aliases=["-v"])] = None,
) -> None:
    """Index vault files. Runs automatically before search/explore.

    Incremental — only re-indexes files whose mtime changed.

    Examples:
        memex index
        memex index -v personal
    """
    result = do_index(vault=vault)
    _output(result, formatter=_format_index)


def vault_add(
    name: Positional[str],
    paths: Positional[list[str]],
    model: str | None = None,
    ignore: list[str] | None = None,
) -> None:
    """Add directories to a vault (creates it if new).

    A vault is a named group of directories that share an embedding model.
    Adding paths to an existing vault appends them.

    Examples:
        memex vault:add personal ~/notes ~/journal
        memex vault:add work ~/work-docs --model some/other-model
        memex vault:add work ~/work-docs --ignore "*.generated.md"
    """
    config = load_config()

    resolved_paths = [Path(p).expanduser().resolve() for p in paths]
    for p in resolved_paths:
        if not p.is_dir():
            print(f"Error: Not a directory: {p}", file=sys.stderr)
            sys.exit(1)

    if name in config.vaults:
        existing = config.vaults[name]
        existing_set = set(existing.paths)
        for p in resolved_paths:
            if p not in existing_set:
                existing.paths.append(p)
        if model is not None:
            existing.model = model
        if ignore is not None:
            existing.ignore = ignore
    else:
        config.vaults[name] = VaultConfig(
            name=name,
            paths=resolved_paths,
            model=model or config.default_model,
            ignore=ignore or [],
        )

    save_config(config)
    vault = config.vaults[name]
    print(f"Vault '{name}': {len(vault.paths)} path(s), model={vault.model}")
    for p in vault.paths:
        print(f"  {p}")


def vault_list() -> None:
    """List all configured vaults."""
    config = load_config()
    if not config.vaults:
        print("No vaults configured. Use 'memex vault:add <name> <path>...' to add one.")
        return

    for name, vault in sorted(config.vaults.items()):
        model_info = f" (model: {vault.model})" if vault.model != config.default_model else ""
        print(f"{name}{model_info}")
        for p in vault.paths:
            print(f"  {p}")


def vault_info(
    name: Positional[str],
) -> None:
    """Show vault details: paths, model, note counts, DB size.

    Examples:
        memex vault:info personal
    """
    config = load_config()
    if name not in config.vaults:
        print(f"Error: Unknown vault '{name}'. Available: {list(config.vaults.keys())}", file=sys.stderr)
        sys.exit(1)

    vc = config.vaults[name]
    db_file = db_path_for_vault(name)

    print(f"{name}")
    print(f"  model: {vc.model}")

    if db_file.exists():
        db_size_mb = db_file.stat().st_size / (1024 * 1024)
        print(f"  db: {db_file} ({db_size_mb:.1f} MB)")
    else:
        print("  db: not yet created")

    conn = _ensure_indexed(name, vc, global_ignore=config.ignore)
    total = 0
    print("  paths:")
    for p in vc.paths:
        row = conn.execute("SELECT COUNT(*) as cnt FROM notes WHERE root = ?", (str(p),)).fetchone()
        count = row["cnt"]
        total += count
        print(f"    {p} — {count} notes")
    conn.close()

    print(f"  total: {total} notes")


def vault_remove(
    name: Positional[str],
    path: str | None = None,
) -> None:
    """Remove a vault or a specific path from it.

    Without --path, removes the entire vault. With --path, removes only
    that directory (deletes the vault if it was the last path).

    Examples:
        memex vault:remove personal
        memex vault:remove personal --path ~/journal
    """
    config = load_config()
    if name not in config.vaults:
        print(f"Error: Unknown vault '{name}'", file=sys.stderr)
        sys.exit(1)

    if path:
        resolved = Path(path).expanduser().resolve()
        vault = config.vaults[name]
        vault.paths = [p for p in vault.paths if p != resolved]
        if not vault.paths:
            del config.vaults[name]
            print(f"Removed vault '{name}' (last path removed)")
        else:
            print(f"Removed {resolved} from vault '{name}'")
    else:
        del config.vaults[name]
        print(f"Removed vault '{name}'")

    save_config(config)


def logs(
    lines: Annotated[int, tyro.conf.arg(aliases=["-n"])] = 20,
) -> None:
    """Show recent log entries.

    Examples:
        memex logs
        memex logs -n 100
    """
    if not LOG_FILE.exists():
        print("No log file yet.", file=sys.stderr)
        return
    all_lines = LOG_FILE.read_text().splitlines()
    for line in all_lines[-lines:]:
        print(line)


def config() -> None:
    """Print the config file.

    Examples:
        memex config
    """
    if not CONFIG_PATH.exists():
        print("No config file yet. Use 'memex vault:add <name> <path>...' to create one.", file=sys.stderr)
        return
    print(CONFIG_PATH.read_text(), end="")


# ── Output ───────────────────────────────────────────────────────────────────


def _output(result: dict, *, formatter) -> None:
    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    text = formatter(result)
    if text:
        print(text)


# ── Business logic ───────────────────────────────────────────────────────────


def _get_vault_config(config: Config, vault_name: str | None) -> list[tuple[str, VaultConfig]]:
    """Resolve vault name to config(s). Returns all vaults if name is None."""
    if vault_name is None:
        return list(config.vaults.items())
    if vault_name not in config.vaults:
        return []
    return [(vault_name, config.vaults[vault_name])]


def _ensure_indexed(vault_name: str, vc: VaultConfig, global_ignore: list[str] | None = None) -> sqlite3.Connection:
    """Open connection, index vault, return connection."""
    conn = get_connection(db_path_for_vault(vault_name))
    model_name = vc.model if vc.semantic_enabled else None
    embedding_dim = get_embedding_dim(vc.model) if vc.semantic_enabled else None
    ignore = (global_ignore or []) + vc.ignore
    index_vault(conn, vc.paths, model_name=model_name, embedding_dim=embedding_dim, ignore=ignore or None)
    return conn


def do_search(
    query: str,
    vault: str | None = None,
    limit: int = 5,
    page: int = 1,
    concise: bool = True,
) -> dict:
    start_time = time.monotonic()
    config = load_config()

    if not config.vaults:
        return {"error": "No vaults configured. Run 'memex vault:add <name> <path>...' first."}

    vaults = _get_vault_config(config, vault)
    if not vaults:
        return {"error": f"Unknown vault '{vault}'. Available: {list(config.vaults.keys())}"}

    result: dict = {}

    for vault_name, vc in vaults:
        if not vc.semantic_enabled:
            result[vault_name] = {"skipped": "semantic search disabled (model=none)"}
            continue
        conn = _ensure_indexed(vault_name, vc, global_ignore=config.ignore)
        query_embedding = embed_text(query, vc.model)
        hits = search_semantic(conn, query_embedding, limit=page * limit)
        conn.close()

        page_hits = hits[(page - 1) * limit : page * limit]
        if not page_hits:
            continue
        if concise:
            result[vault_name] = [note.path for note, _dist in page_hits]
        else:
            result[vault_name] = [
                {
                    "path": note.path,
                    "title": note.title,
                    "aliases": note.aliases,
                    "tags": note.tags,
                    "content": note.content,
                }
                for note, _dist in page_hits
            ]

    if not result:
        result = {"message": f"No results for '{query}'", "vaults_searched": [v for v, _ in vaults]}

    elapsed = time.monotonic() - start_time
    chars = len(_json.dumps(result))
    n_results = sum(len(v) for v in result.values() if isinstance(v, list))
    log.info(
        'search(query="%s", vault=%s, limit=%d, page=%d) -> %d results, ~%d chars (~%d tokens) in %.2fs',
        query,
        vault,
        limit,
        page,
        n_results,
        chars,
        chars // 4,
        elapsed,
    )
    return result


def do_find(
    query: str,
    vault: str | None = None,
    limit: int = 10,
) -> dict:
    start_time = time.monotonic()
    config = load_config()

    if not config.vaults:
        return {"error": "No vaults configured. Run 'memex vault:add <name> <path>...' first."}

    vaults = _get_vault_config(config, vault)
    if not vaults:
        return {"error": f"Unknown vault '{vault}'. Available: {list(config.vaults.keys())}"}

    all_notes: list[tuple[str, str, list[str]]] = []
    vault_for_path: dict[str, str] = {}

    for vault_name, vc in vaults:
        conn = _ensure_indexed(vault_name, vc, global_ignore=config.ignore)
        notes = get_all_findable(conn)
        conn.close()
        for path, title, aliases in notes:
            all_notes.append((path, title, aliases))
            vault_for_path[path] = vault_name

    hits = find_notes(all_notes, query, limit=limit)

    result: dict = {}
    for hit in hits:
        vn = vault_for_path[hit.path]
        result.setdefault(vn, []).append(hit.path)

    if not result:
        result = {"message": f"No matches for '{query}'", "vaults_searched": [v for v, _ in vaults]}

    elapsed = time.monotonic() - start_time
    n_results = sum(len(v) for v in result.values() if isinstance(v, list))
    log.info(
        'find(query="%s", vault=%s, limit=%d) -> %d results in %.3fs',
        query,
        vault,
        limit,
        n_results,
        elapsed,
    )
    return result


def do_explore(
    note_path: str,
    vault: str,
    concise: bool = False,
) -> dict:
    start_time = time.monotonic()
    config = load_config()

    if vault not in config.vaults:
        return {"error": f"Unknown vault '{vault}'. Available: {list(config.vaults.keys())}"}

    vc = config.vaults[vault]
    conn = _ensure_indexed(vault, vc, global_ignore=config.ignore)

    note, error = resolve_note_for_user(conn, note_path)
    if error:
        conn.close()
        return {"error": error}
    assert note is not None

    outlink_targets = get_outlinks(conn, note.root, note.path)
    note_name = path_to_note_name(note.path)
    backlink_paths = get_backlinks(conn, note_name)

    similar_notes: list[tuple[IndexedNote, float]] = []
    if vc.semantic_enabled:
        embedding = get_note_embedding(conn, note.path)
        if embedding is not None:
            candidates = search_semantic(conn, embedding, limit=10)
            excluded_paths = {note.path} | set(backlink_paths)
            for candidate, distance in candidates:
                if candidate.path not in excluded_paths:
                    similar_notes.append((candidate, distance))
                if len(similar_notes) >= 5:
                    break

    conn.close()

    def format_outlink(target: str, resolved: list[str]) -> dict:
        if not resolved:
            return {"target": target, "resolved_path": None}
        if len(resolved) == 1:
            return {"target": target, "resolved_path": resolved[0]}
        return {"target": target, "resolved_paths": resolved}

    if concise:
        result = {
            "vault": vault,
            "note": {"path": note.path, "title": note.title},
            "outlinks": [format_outlink(t, r) for t, r in outlink_targets],
            "backlinks": [{"path": p} for p in backlink_paths],
            "similar": [{"path": n.path, "title": n.title, "distance": round(d, 3)} for n, d in similar_notes],
        }
    else:
        result = {
            "vault": vault,
            "note": {
                "path": note.path,
                "title": note.title,
                "aliases": note.aliases,
                "tags": note.tags,
                "content": note.content,
            },
            "outlinks": [format_outlink(t, r) for t, r in outlink_targets],
            "backlinks": [{"path": p} for p in backlink_paths],
            "similar": [{"path": n.path, "title": n.title, "distance": round(d, 3)} for n, d in similar_notes],
        }

    elapsed = time.monotonic() - start_time
    chars = len(_json.dumps(result))
    log.info(
        'explore(path="%s", vault="%s") -> outlinks=%d, backlinks=%d, similar=%d, ~%d chars (~%d tokens) in %.2fs',
        note_path,
        vault,
        len(outlink_targets),
        len(backlink_paths),
        len(similar_notes),
        chars,
        chars // 4,
        elapsed,
    )
    return result


def do_rename(
    note_path: str,
    new_name: str,
    vault: str,
) -> dict:
    config = load_config()

    if vault not in config.vaults:
        return {"error": f"Unknown vault '{vault}'. Available: {list(config.vaults.keys())}"}

    vc = config.vaults[vault]
    conn = _ensure_indexed(vault, vc, global_ignore=config.ignore)

    note, error = resolve_note_for_user(conn, note_path)
    if error:
        conn.close()
        return {"error": error}
    assert note is not None

    root_path = Path(note.root)
    old_path = note.path
    old_title = path_to_note_name(old_path)
    old_path_without_ext = old_path.removesuffix(".md")

    old_file_path = root_path / old_path
    new_filename = new_name if new_name.endswith(".md") else f"{new_name}.md"
    new_path = str(Path(old_path).parent / new_filename)
    if new_path.startswith("./"):
        new_path = new_path[2:]
    new_file_path = root_path / new_path
    new_title = new_name.removesuffix(".md")
    new_path_without_ext = new_path.removesuffix(".md")

    if new_file_path.exists():
        conn.close()
        return {"error": f"Target already exists: {new_path}"}

    links = find_links_to_note(conn, old_path)

    files_with_same_title = get_files_with_title(conn, old_title)
    is_preferred_for_title = True
    if len(files_with_same_title) > 1:
        sorted_files = sorted(files_with_same_title, key=lambda p: (p.lower() != p, p.lower()))
        is_preferred_for_title = sorted_files[0] == old_path

    files_with_same_path = get_files_with_path(conn, old_path_without_ext)
    is_preferred_for_path = True
    if len(files_with_same_path) > 1:
        sorted_files = sorted(files_with_same_path, key=lambda p: (p.lower() != p, p.lower()))
        is_preferred_for_path = sorted_files[0] == old_path

    links_by_source: dict[str, list[tuple[str, str]]] = {}
    skipped_ambiguous: list[tuple[str, str]] = []

    for source_path, target_raw, link_type in links:
        if link_type in ("title", "title_ambiguous"):
            if not is_preferred_for_title:
                skipped_ambiguous.append((source_path, target_raw))
                continue
            links_by_source.setdefault(source_path, []).append((target_raw, "title"))
            continue
        if link_type in ("path", "path_ambiguous"):
            if not is_preferred_for_path:
                skipped_ambiguous.append((source_path, target_raw))
                continue
            links_by_source.setdefault(source_path, []).append((target_raw, "path"))
            continue
        skipped_ambiguous.append((source_path, target_raw))

    old_file_path.rename(new_file_path)

    updated_files = []
    for source_path, link_list in links_by_source.items():
        source_file = _find_file_in_roots(vc.paths, source_path)
        if source_file is None or not source_file.exists():
            continue

        content = source_file.read_text(encoding="utf-8")
        new_content = content

        for target_raw, link_type in link_list:
            if link_type == "path":
                pattern = re.compile(
                    r"\[\[" + re.escape(target_raw) + r"(\s*[#|][^\]]*)?(\]\])",
                    re.IGNORECASE,
                )
                new_content = pattern.sub(f"[[{new_path_without_ext}\\1\\2", new_content)
            else:
                pattern = re.compile(
                    r"\[\[" + re.escape(target_raw) + r"(\s*[#|][^\]]*)?(\]\])",
                    re.IGNORECASE,
                )
                new_content = pattern.sub(f"[[{new_title}\\1\\2", new_content)

        if new_content != content:
            source_file.write_text(new_content, encoding="utf-8")
            updated_files.append(source_path)

    conn.close()

    conn = _ensure_indexed(vault, vc, global_ignore=config.ignore)
    conn.close()

    result: dict = {
        "old_path": old_path,
        "new_path": new_path,
        "updated_files": updated_files,
        "updated_count": len(updated_files),
    }

    if skipped_ambiguous:
        result["skipped_ambiguous"] = [{"source": src, "link": link} for src, link in skipped_ambiguous]
        result["warning"] = (
            f"Skipped {len(skipped_ambiguous)} ambiguous link(s). "
            "These are title-based links where multiple files share the same title. "
            "Update them manually or use path-based links."
        )

    return result


def do_index(vault: str | None = None) -> dict:
    config = load_config()
    if not config.vaults:
        return {"error": "No vaults configured. Run 'memex vault:add <name> <path>...' first."}

    vaults = _get_vault_config(config, vault)
    if not vaults:
        return {"error": f"Unknown vault '{vault}'. Available: {list(config.vaults.keys())}"}

    result = {}
    for vault_name, vc in vaults:
        conn = _ensure_indexed(vault_name, vc, global_ignore=config.ignore)

        row = conn.execute("SELECT COUNT(*) as cnt FROM notes").fetchone()
        total = row["cnt"]
        conn.close()

        result[vault_name] = {"total": total, "paths": [str(p) for p in vc.paths]}

    return result


# ── Utilities ────────────────────────────────────────────────────────────────


def resolve_note_for_user(conn: sqlite3.Connection, note_path: str) -> tuple[IndexedNote | None, str | None]:
    """Resolve user-provided note path to a specific note with ambiguity detection.

    Returns (note, None) on success, (None, error_message) on failure.
    """
    note = get_note(conn, note_path)
    path_without_ext = note_path.removesuffix(".md")

    if "/" in note_path:
        if not note:
            matching_paths = get_files_with_path(conn, path_without_ext)
            if len(matching_paths) > 1:
                paths_str = ", ".join(sorted(matching_paths))
                return None, f"Multiple notes match path '{note_path}': {paths_str}. Use exact case."
            if len(matching_paths) == 1:
                note = get_note(conn, matching_paths[0])
    else:
        matching_paths = get_files_with_title(conn, path_without_ext)
        if len(matching_paths) > 1:
            paths_str = ", ".join(sorted(matching_paths))
            return None, f"Multiple notes with title '{path_without_ext}': {paths_str}. Specify full path."
        if not note and len(matching_paths) == 1:
            note = get_note(conn, matching_paths[0])

    if not note:
        return None, f"Note not found: {note_path}"

    return note, None


def path_to_note_name(path: str) -> str:
    """Convert a note path to the name used in wikilinks (filename without .md)."""
    return Path(path).stem


def _find_file_in_roots(roots: list[Path], rel_path: str) -> Path | None:
    """Find a file across multiple root directories."""
    for root in roots:
        candidate = root / rel_path
        if candidate.exists():
            return candidate
    return None


# ── Text formatters ──────────────────────────────────────────────────────────


def _format_grouped_paths(
    paths: list[str],
    suffix: Callable[[int], str] | None = None,
) -> list[str]:
    """Group paths by parent directory for compact display.

    Returns indented lines: directory header at indent 2, filenames at indent 4.
    Optional suffix callback receives the original index and appends to the filename.
    """
    grouped: dict[str, list[tuple[str, int]]] = {}
    for i, p in enumerate(paths):
        parent = str(Path(p).parent)
        grouped.setdefault(parent, []).append((Path(p).name, i))

    lines: list[str] = []
    for parent in sorted(grouped):
        header = f"{parent}/" if parent != "." else ""
        if header:
            lines.append(f"  {header}")
        for name, idx in sorted(grouped[parent]):
            extra = suffix(idx) if suffix else ""
            indent = "    " if header else "  "
            lines.append(f"{indent}{name}{extra}")
    return lines


def _format_find(result: dict) -> str:
    if "message" in result:
        return result["message"]

    lines = []
    for vault_name, paths in result.items():
        if not isinstance(paths, list):
            continue
        if len(result) > 1:
            lines.append(vault_name)
        for path in paths:
            indent = "  " if len(result) > 1 else ""
            lines.append(f"{indent}{path}")

    return "\n".join(lines)


def _format_search(result: dict) -> str:
    if "message" in result:
        return result["message"]

    lines = []
    for vault_name, entries in result.items():
        if isinstance(entries, dict) and "skipped" in entries:
            lines.append(f"{vault_name}: {entries['skipped']}")
            continue
        if not isinstance(entries, list):
            continue
        lines.append(vault_name)
        for entry in entries:
            if isinstance(entry, str):
                lines.append(f"  {entry}")
            elif isinstance(entry, dict):
                path = entry["path"]
                tags = " ".join(f"#{t}" for t in entry.get("tags", []))
                aliases = ", ".join(entry.get("aliases", []))
                lines.append(f"\n--- {path} [{vault_name}] ---")
                if tags:
                    lines.append(f"tags: {tags}")
                if aliases:
                    lines.append(f"aliases: {aliases}")
                lines.append("")
                lines.append(entry.get("content", ""))
        lines.append("")

    return "\n".join(lines).rstrip()


def _format_explore(result: dict) -> str:
    lines = []
    vault_name = result["vault"]
    note = result["note"]

    if "content" in note:
        tags = " ".join(f"#{t}" for t in note.get("tags", []))
        aliases = ", ".join(note.get("aliases", []))
        lines.append(f"--- {note['path']} [{vault_name}] ---")
        if tags:
            lines.append(f"tags: {tags}")
        if aliases:
            lines.append(f"aliases: {aliases}")
        lines.append("")
        lines.append(note["content"])
    else:
        lines.append(f"{note['path']} [{vault_name}]")

    if result["outlinks"]:
        lines.append("")
        lines.append("Outlinks:")

        unresolved: list[str] = []
        resolved_paths: list[str] = []
        for link in result["outlinks"]:
            paths = link.get("resolved_paths") or ([link["resolved_path"]] if link.get("resolved_path") else [])
            if not paths:
                unresolved.append(link["target"])
            else:
                resolved_paths.extend(paths)

        lines.extend(_format_grouped_paths(resolved_paths))
        if unresolved:
            lines.append("  (unresolved)")
            for target in sorted(unresolved):
                lines.append(f"    {target}")

    if result["backlinks"]:
        lines.append("")
        lines.append("Backlinks:")
        lines.extend(_format_grouped_paths([bl["path"] for bl in result["backlinks"]]))

    if result["similar"]:
        lines.append("")
        lines.append("Similar:")
        lines.extend(
            _format_grouped_paths(
                [s["path"] for s in result["similar"]],
                suffix=lambda i: f"  ({result['similar'][i]['distance']:.3f})",
            )
        )

    return "\n".join(lines)


def _format_rename(result: dict) -> str:
    lines = [f"Renamed: {result['old_path']} -> {result['new_path']}"]

    if result["updated_files"]:
        lines.append(f"Updated {result['updated_count']} file(s):")
        for f in result["updated_files"]:
            lines.append(f"  {f}")
    else:
        lines.append("No links to update.")

    if result.get("warning"):
        lines.append(f"Warning: {result['warning']}")
        for item in result.get("skipped_ambiguous", []):
            lines.append(f"  {item['source']} [[{item['link']}]]")

    return "\n".join(lines)


def _format_index(result: dict) -> str:
    lines = []
    for vault_name, stats in result.items():
        total = stats["total"]
        n_paths = len(stats.get("paths", []))
        lines.append(f"{vault_name}: {total} notes, {n_paths} path(s)")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
