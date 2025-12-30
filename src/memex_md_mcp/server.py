"""MCP server for semantic search over markdown vaults."""

import json
import os
import time
from importlib.metadata import metadata
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from memex_md_mcp.db import (
    IndexedNote,
    get_backlinks,
    get_connection,
    get_note,
    get_note_embedding,
    get_outlinks,
    search_fts,
    search_semantic,
)
from memex_md_mcp.embeddings import embed_text
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

    Args:
        query: Describe what you're looking for in natural language. Use 1-3 sentences for best
               results. Question format works well ("What...?", "How did we...?").
               If None, runs FTS-only mode using keywords (useful for exact term lookup).
               (e.g., "What authentication approach did we decide on? I remember we discussed OAuth vs sessions.")
        keywords: Optional list of exact terms to match. Use for specific names, acronyms,
                  or technical terms. Notes containing these get boosted in results.
                  Required if query is None.
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

    # Semantic search (only if query provided)
    semantic_results: list[tuple[IndexedNote, float]] = []
    if query:
        query_embedding = embed_text(query)
        semantic_results = search_semantic(conn, query_embedding, vault=vault, limit=fetch_limit)

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
    if not page_results:
        result: dict = {"message": f"No results for '{search_desc}' (page {page})", "vaults_searched": list(vaults.keys())}
    elif concise:
        # Group paths by vault for token efficiency
        grouped: dict[str, list[str]] = {}
        for r in page_results:
            grouped.setdefault(r.vault, []).append(r.path)
        result = grouped
    else:
        # Group full results by vault
        grouped_full: dict[str, list[dict]] = {}
        for r in page_results:
            grouped_full.setdefault(r.vault, []).append({
                "path": r.path,
                "title": r.title,
                "aliases": r.aliases,
                "tags": r.tags,
                "content": r.content,
            })
        result = grouped_full

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

    The combination helps you understand both the explicit graph structure (wikilinks)
    and implicit conceptual relationships (embeddings).

    Args:
        note_path: Relative path within the vault
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
    if not note:
        conn.close()
        return {"error": f"Note not found: {vault}/{note_path}"}

    outlink_targets = get_outlinks(conn, vault, note_path)
    note_name = path_to_note_name(note_path)
    backlink_paths = get_backlinks(conn, vault, note_name)

    # Find semantically similar notes that aren't already linked
    similar_notes: list[tuple[IndexedNote, float]] = []
    embedding = get_note_embedding(conn, vault, note_path)
    if embedding is not None:
        candidates = search_semantic(conn, embedding, vault=vault, limit=10)  # fetch extra to filter
        excluded_paths = {note_path} | set(backlink_paths)
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
