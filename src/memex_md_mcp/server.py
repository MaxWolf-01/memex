"""MCP server for semantic search over markdown vaults."""

import json
import os
import time
from importlib.metadata import metadata
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from memex_md_mcp.db import (
    IndexedNote,
    find_links_to_note,
    get_backlinks,
    get_connection,
    get_files_with_path,
    get_files_with_title,
    get_note,
    get_note_embedding,
    get_outlinks,
    search_fts,
    search_semantic,
)
from memex_md_mcp.embeddings import embed_text, is_semantic_enabled
from memex_md_mcp.indexer import index_all_vaults
from memex_md_mcp.logging import get_logger

log = get_logger()

mcp = FastMCP(name="memex")


def parse_vaults_env() -> dict[str, Path]:
    """Parse MEMEX_VAULTS env var into {vault_id: path} dict.

    Vault ID is the absolute path string, avoiding collisions when multiple vaults
    have the same folder name.
    """
    vaults_env = os.environ.get("MEMEX_VAULTS", "")
    if not vaults_env:
        return {}
    vaults = {}
    for path_str in vaults_env.split(":"):
        path_str = path_str.strip()
        if not path_str:
            continue
        path = Path(path_str).expanduser().resolve()
        vault_id = str(path)
        vaults[vault_id] = path
    return vaults


def resolve_vault_path(vault: str | None, vaults: dict[str, Path]) -> str | None:
    """Resolve user-provided vault path to match configured vault IDs.

    Users may provide relative paths (./agent) or paths with ~ that need resolution.
    Vault IDs in the vaults dict are always absolute resolved paths.
    """
    if vault is None:
        return None
    resolved = str(Path(vault).expanduser().resolve())
    return resolved if resolved in vaults else vault


def sanitize_for_fts(keywords: list[str]) -> str:
    """Sanitize keywords for FTS5 query. Strips problematic punctuation."""
    sanitized = []
    for kw in keywords:
        # Replace hyphens with space, remove apostrophes and other problematic chars
        clean = kw.replace("-", " ").replace("'", "").replace('"', "")
        # Keep only alphanumeric and spaces
        clean = "".join(c if c.isalnum() or c.isspace() else " " for c in clean)
        clean = " ".join(clean.split())  # normalize whitespace
        if clean:
            sanitized.append(clean)
    return " ".join(sanitized)


def rrf_fusion(
    semantic_results: list[tuple[IndexedNote, float]],
    fts_results: list[IndexedNote],
    k: int = 20,
) -> list[IndexedNote]:
    """Reciprocal Rank Fusion of semantic and FTS results."""
    scores: dict[tuple[str, str], float] = {}
    notes: dict[tuple[str, str], IndexedNote] = {}

    # Score semantic results by rank
    for rank, (note, _distance) in enumerate(semantic_results):
        key = (note.vault, note.path)
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
        notes[key] = note

    # Score FTS results by rank
    for rank, note in enumerate(fts_results):
        key = (note.vault, note.path)
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
        notes[key] = note

    # Sort by combined score (descending)
    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [notes[key] for key in sorted_keys]


@mcp.tool()
def search(
    query: str | None = None,
    keywords: list[str] | None = None,
    vault: str | None = None,
    limit: int = 5,
    page: int = 1,
    concise: bool = True,
) -> dict:
    """Search across markdown vaults using semantic search, optionally boosted by keyword matching.

    Semantic search finds conceptually related notes based on meaning. If keywords are provided,
    full-text search results are fused in using RRF (Reciprocal Rank Fusion) to boost notes
    containing those exact terms.

    Note: Semantic search can be disabled via MEMEX_DISABLE_SEMANTIC=1. When disabled, only
    keyword-based FTS is available; the query parameter will be ignored.

    Args:
        query: Describe what you're looking for in natural language. Use 1-3 sentences for best
               results. Question format works well ("What...?", "How did we...?").
               If None, runs FTS-only mode using keywords (useful for exact term lookup).
               Ignored if semantic search is disabled.
               (e.g., "What authentication approach did we decide on? I remember we discussed OAuth vs sessions.")
        keywords: Optional list of exact terms to match. Use for specific names, acronyms,
                  or technical terms. Notes containing these get boosted in results.
                  Required if query is None or semantic search is disabled.
                  (e.g., ["OAuth", "JWT", "session"])
        vault: Specific vault to search (None = all vaults)
        limit: Maximum number of results per page
        page: Page number (1-indexed). Use to get more results beyond the first page.
        concise: If True (default), return only paths grouped by vault. If False, full content.

    Returns:
        Results grouped by vault absolute path. Keys are vault paths, values are lists of
        note paths (concise) or note dicts with path/title/aliases/tags/content (full).
    """
    start_time = time.monotonic()
    vaults = parse_vaults_env()

    if not vaults:
        return {"error": "No vaults configured. Set MEMEX_VAULTS env var."}

    if not query and not keywords:
        return {"error": "Provide query (semantic search) or keywords (FTS), or both."}

    vault = resolve_vault_path(vault, vaults)
    if vault is not None and vault not in vaults:
        return {"error": f"Vault '{vault}' not found. Available: {list(vaults.keys())}"}

    conn = get_connection()
    index_all_vaults(conn, vaults, on_progress=lambda _: None)

    # Fetch enough results to cover requested page
    fetch_limit = page * limit

    # Semantic search (only if query provided and enabled)
    semantic_results: list[tuple[IndexedNote, float]] = []
    semantic_enabled = is_semantic_enabled()
    semantic_skipped = False
    if query and semantic_enabled:
        query_embedding = embed_text(query)
        semantic_results = search_semantic(conn, query_embedding, vault=vault, limit=fetch_limit)
    elif query and not semantic_enabled:
        semantic_skipped = True

    # FTS search (only if keywords provided)
    fts_results: list[IndexedNote] = []
    if keywords:
        fts_query = sanitize_for_fts(keywords)
        if fts_query:
            try:
                fts_results = search_fts(conn, fts_query, vault=vault, limit=fetch_limit)
            except Exception as e:
                log.warning("FTS search failed for keywords %s: %s", keywords, e)

    conn.close()

    # Combine results
    if semantic_results and fts_results:
        combined = rrf_fusion(semantic_results, fts_results, k=20)
    elif semantic_results:
        combined = [note for note, _dist in semantic_results]
    else:
        combined = fts_results

    configured_vault_names = set(vaults.keys())
    combined = [n for n in combined if n.vault in configured_vault_names]

    # Paginate
    offset = (page - 1) * limit
    page_results = combined[offset : offset + limit]

    search_desc = query if query else f"keywords={keywords}"
    result: dict = {}
    if not page_results:
        result = {"message": f"No results for '{search_desc}' (page {page})", "vaults_searched": list(vaults.keys())}
    elif concise:
        # Group paths by vault for token efficiency
        for r in page_results:
            result.setdefault(r.vault, []).append(r.path)
    else:
        # Group full results by vault
        for r in page_results:
            result.setdefault(r.vault, []).append({
                "path": r.path,
                "title": r.title,
                "aliases": r.aliases,
                "tags": r.tags,
                "content": r.content,
            })

    if semantic_skipped:
        result["info"] = "Semantic search disabled, query parameter ignored. Use keywords for FTS."

    elapsed = time.monotonic() - start_time
    chars = len(json.dumps(result))
    log.info(
        'search(query="%s", keywords=%s, vault=%s, limit=%d, page=%d) -> %d results, ~%d chars (~%d tokens) in %.2fs',
        query,
        keywords,
        vault,
        limit,
        page,
        len(page_results),
        chars,
        chars // 4,
        elapsed,
    )
    return result


def path_to_note_name(path: str) -> str:
    """Convert a note path to the name used in wikilinks (filename without .md)."""
    return Path(path).stem


@mcp.tool()
def explore(
    note_path: str,
    vault: str,
    concise: bool = False,
) -> dict:
    """Explore the neighborhood of a specific note.

    Use after search() to understand a note's context. Returns three types of connections:

    - **outlinks**: Notes this note links to via [[wikilinks]]. Shows intentional references.
      A null resolved_path means the target is referenced but doesn't exist yet.
    - **backlinks**: Notes that link TO this note. Shows what depends on or references this concept.
    - **similar**: Semantically similar notes that AREN'T already linked. Surfaces hidden
      connections - notes about related concepts that might be worth linking.
      Empty if MEMEX_DISABLE_SEMANTIC=1.

    The combination helps you understand both the explicit graph structure (wikilinks)
    and implicit conceptual relationships (embeddings).

    Args:
        note_path: Path or title of the note. Can be:
            - Full path: "plans/revision1.md" or "plans/revision1"
            - Just title: "revision1" (if unique in vault; errors if ambiguous)
        vault: The vault containing the note
        concise: If True, return only paths/titles for linked notes (no full content).
                 If False (default), include full content for the main note.
    """
    start_time = time.monotonic()
    vaults = parse_vaults_env()
    if not vaults:
        return {"error": "No vaults configured. Set MEMEX_VAULTS env var."}

    vault = resolve_vault_path(vault, vaults) or vault
    if vault not in vaults:
        return {"error": f"Vault '{vault}' not found. Available: {list(vaults.keys())}"}

    conn = get_connection()
    index_all_vaults(conn, {vault: vaults[vault]}, on_progress=lambda _: None)

    note = get_note(conn, vault, note_path)
    resolved_path = note.path if note else None

    # When path has no "/" it could be a title - check for ambiguity
    if "/" not in note_path:
        title = note_path.removesuffix(".md")
        matching_paths = get_files_with_title(conn, vault, title)
        if len(matching_paths) > 1:
            conn.close()
            paths_str = ", ".join(matching_paths)
            return {"error": f"Multiple notes with title '{title}': {paths_str}. Specify full path."}
        # If no direct match but one title match, use it
        if not note and len(matching_paths) == 1:
            resolved_path = matching_paths[0]
            note = get_note(conn, vault, resolved_path)

    if not note or not resolved_path:
        conn.close()
        return {"error": f"Note not found: {note_path}"}

    outlink_targets = get_outlinks(conn, vault, resolved_path)
    note_name = path_to_note_name(resolved_path)
    backlink_paths = get_backlinks(conn, vault, note_name)

    # Find semantically similar notes that aren't already linked (skip if semantic disabled)
    similar_notes: list[tuple[IndexedNote, float]] = []
    embedding = get_note_embedding(conn, vault, resolved_path) if is_semantic_enabled() else None
    if embedding is not None:
        candidates = search_semantic(conn, embedding, vault=vault, limit=10)  # fetch extra to filter
        excluded_paths = {resolved_path} | set(backlink_paths)
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
            "note": {"vault": note.vault, "path": note.path, "title": note.title},
            "outlinks": [format_outlink(t, r) for t, r in outlink_targets],
            "backlinks": [{"path": p} for p in backlink_paths],
            "similar": [{"path": n.path, "title": n.title, "distance": round(d, 3)} for n, d in similar_notes],
        }
    else:
        result = {
            "note": {
                "vault": note.vault,
                "path": note.path,
                "title": note.title,
                "aliases": note.aliases,
                "tags": note.tags,
                "content": note.content,
            },
            "outlinks": [format_outlink(t, r) for t, r in outlink_targets],
            "backlinks": [{"path": p} for p in backlink_paths],
            "similar": [
                {"vault": n.vault, "path": n.path, "title": n.title, "distance": round(d, 3)}
                for n, d in similar_notes
            ],
        }

    elapsed = time.monotonic() - start_time
    chars = len(json.dumps(result))
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


@mcp.tool()
def rename(
    note_path: str,
    new_name: str,
    vault: str,
) -> dict:
    """Rename a note and update all wikilinks pointing to it.

    Renames the file on disk and updates all [[wikilinks]] in other notes that reference
    the old name. This keeps the wikilink graph consistent after renaming.

    Handles edge cases:
    - Path-based links: [[subdir/note]] updated to [[subdir/newname]]
    - Title-based links: [[note]] updated to [[newname]]
    - Ambiguous links: When multiple files share a title, only path-based links are updated
    - Case variants: [[Note]] and [[note]] both updated (matching Obsidian behavior)

    Args:
        note_path: Current path of the note to rename (extension .md is optional)
        new_name: New filename (without path, without .md extension)
        vault: The vault containing the note

    Returns:
        Dict with renamed path, updated files, and any warnings about skipped links.
    """
    import re

    vaults = parse_vaults_env()
    if not vaults:
        return {"error": "No vaults configured. Set MEMEX_VAULTS env var."}

    vault = resolve_vault_path(vault, vaults) or vault
    if vault not in vaults:
        return {"error": f"Vault '{vault}' not found. Available: {list(vaults.keys())}"}

    vault_path = vaults[vault]
    conn = get_connection()
    index_all_vaults(conn, {vault: vault_path}, on_progress=lambda _: None)

    # Resolve source note
    note = get_note(conn, vault, note_path)
    if not note:
        conn.close()
        return {"error": f"Note not found: {note_path}"}

    old_path = note.path
    old_title = path_to_note_name(old_path)
    old_path_without_ext = old_path.removesuffix(".md")

    # Build new path (preserve directory if any)
    old_file_path = vault_path / old_path
    new_filename = new_name if new_name.endswith(".md") else f"{new_name}.md"
    new_path = str(Path(old_path).parent / new_filename)
    if new_path.startswith("./"):
        new_path = new_path[2:]
    new_file_path = vault_path / new_path
    new_title = new_name.removesuffix(".md")
    new_path_without_ext = new_path.removesuffix(".md")

    # Check target doesn't exist
    if new_file_path.exists():
        conn.close()
        return {"error": f"Target already exists: {new_path}"}

    # Find all links that resolve to this note
    links = find_links_to_note(conn, vault, old_path)

    # Check if this file is the "preferred" one for title-based resolution
    # Obsidian prefers lowercase when multiple files have same title (different case)
    files_with_same_title = get_files_with_title(conn, vault, old_title)
    is_preferred_for_title = True
    if len(files_with_same_title) > 1:
        # Sort by lowercase preference (lowercase first), then alphabetically
        sorted_files = sorted(files_with_same_title, key=lambda p: (p.lower() != p, p.lower()))
        is_preferred_for_title = sorted_files[0] == old_path

    # Check if this file is the "preferred" one for path-based resolution
    # Same logic: prefer lowercase when multiple files have same path (different case)
    files_with_same_path = get_files_with_path(conn, vault, old_path_without_ext)
    is_preferred_for_path = True
    if len(files_with_same_path) > 1:
        sorted_files = sorted(files_with_same_path, key=lambda p: (p.lower() != p, p.lower()))
        is_preferred_for_path = sorted_files[0] == old_path

    # Group links by source file
    links_by_source: dict[str, list[tuple[str, str]]] = {}
    skipped_ambiguous: list[tuple[str, str]] = []

    for source_path, target_raw, link_type in links:
        if link_type in ("title", "title_ambiguous"):
            if not is_preferred_for_title:
                # This file isn't the preferred resolution target for title links
                # (title links resolve to a different file with same name)
                skipped_ambiguous.append((source_path, target_raw))
                continue
            # This file IS preferred, so title links resolve here - update them
            links_by_source.setdefault(source_path, []).append((target_raw, "title"))
            continue
        if link_type in ("path", "path_ambiguous"):
            if not is_preferred_for_path:
                # This file isn't the preferred resolution target for path links
                skipped_ambiguous.append((source_path, target_raw))
                continue
            # This file IS preferred, so path links resolve here - update them
            links_by_source.setdefault(source_path, []).append((target_raw, "path"))
            continue
        # Unknown link type - skip safely
        skipped_ambiguous.append((source_path, target_raw))

    # Rename file on disk
    old_file_path.rename(new_file_path)

    # Update wikilinks in source files
    updated_files = []
    for source_path, link_list in links_by_source.items():
        source_file = vault_path / source_path
        if not source_file.exists():
            continue

        content = source_file.read_text(encoding="utf-8")
        new_content = content

        for target_raw, link_type in link_list:
            if link_type == "path":
                # Path-based link: [[subdir/note]] → [[subdir/newname]]
                pattern = re.compile(
                    r"\[\[" + re.escape(target_raw) + r"(\s*[#|][^\]]*)?(\]\])",
                    re.IGNORECASE,
                )
                new_content = pattern.sub(f"[[{new_path_without_ext}\\1\\2", new_content)
            else:
                # Title-based link: [[note]] → [[newname]]
                pattern = re.compile(
                    r"\[\[" + re.escape(target_raw) + r"(\s*[#|][^\]]*)?(\]\])",
                    re.IGNORECASE,
                )
                new_content = pattern.sub(f"[[{new_title}\\1\\2", new_content)

        if new_content != content:
            source_file.write_text(new_content, encoding="utf-8")
            updated_files.append(source_path)

    conn.close()

    # Re-index affected files
    conn = get_connection()
    index_all_vaults(conn, {vault: vault_path}, on_progress=lambda _: None)
    conn.close()

    result: dict = {
        "old_path": old_path,
        "new_path": new_path,
        "updated_files": updated_files,
        "updated_count": len(updated_files),
    }

    if skipped_ambiguous:
        result["skipped_ambiguous"] = [
            {"source": src, "link": link} for src, link in skipped_ambiguous
        ]
        result["warning"] = (
            f"Skipped {len(skipped_ambiguous)} ambiguous link(s). "
            "These are title-based links where multiple files share the same title. "
            "Update them manually or use path-based links."
        )

    return result


@mcp.tool()
def mcp_info() -> str:
    """Get setup instructions and example workflow for this MCP server."""
    readme = metadata("memex-md-mcp").get_payload()  # type: ignore[attr-defined]
    assert readme, "Package metadata missing README content"
    return readme


def main() -> None:
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
