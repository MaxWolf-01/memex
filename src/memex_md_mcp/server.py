"""MCP server for semantic search over markdown vaults."""

import os
from importlib.metadata import metadata
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

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

mcp = FastMCP(
    name="memex",
    instructions="Semantic search over markdown vaults. Use the search tool to find relevant notes.",
)


def parse_vaults_env() -> dict[str, Path]:
    """Parse MEMEX_VAULTS env var into {vault_id: path} dict."""
    vaults_env = os.environ.get("MEMEX_VAULTS", "")
    if not vaults_env:
        return {}
    vaults = {}
    for path_str in vaults_env.split(":"):
        path_str = path_str.strip()
        if not path_str:
            continue
        path = Path(path_str).expanduser().resolve()
        vault_id = path.name  # use folder name as ID
        vaults[vault_id] = path
    return vaults


@mcp.tool()
async def search(
    query: str,
    vault: str | None = None,
    limit: int = 5,
    concise: bool = False,
    ctx: Context | None = None,
) -> list[dict]:
    """Search across markdown vaults using semantic + full-text search.

    Use this to find notes when you don't know exact names or want conceptual matches.
    Combines keyword matching (FTS) with semantic similarity.

    Args:
        query: Search query - can be keywords or natural language
               (e.g., "terraform state locking", "auth system architecture decisions",
               "error handling preferences for this project")
        vault: Specific vault to search (None = all vaults)
        limit: Maximum number of results to return
        concise: If True, return only vault/path/title. If False (default), full content.
    """
    vaults = parse_vaults_env()

    if not vaults:
        return [{"error": "No vaults configured. Set MEMEX_VAULTS env var."}]

    conn = get_connection()

    async def log(msg: str) -> None:
        if ctx:
            await ctx.info(msg)

    await log(f"Checking index for {len(vaults)} vault(s)...")
    stats = index_all_vaults(conn, vaults, on_progress=lambda _: None)

    total_indexed = sum(s.total_in_vault for s in stats.values())
    total_changed = sum(s.total_processed for s in stats.values())
    if total_changed > 0:
        await log(f"Indexed {total_changed} changed files ({total_indexed} total)")

    fts_results = search_fts(conn, query, vault=vault, limit=limit)

    query_embedding = embed_text(query)
    semantic_results = search_semantic(conn, query_embedding, vault=vault, limit=limit)

    conn.close()

    seen: set[tuple[str, str]] = set()
    combined: list[IndexedNote] = []

    for note in fts_results:
        key = (note.vault, note.path)
        if key not in seen:
            seen.add(key)
            combined.append(note)

    for note, _distance in semantic_results:
        key = (note.vault, note.path)
        if key not in seen:
            seen.add(key)
            combined.append(note)

    if not combined:
        return [{"message": f"No results for '{query}'", "vaults_searched": list(vaults.keys())}]

    if concise:
        return [
            {"vault": r.vault, "path": r.path, "title": r.title}
            for r in combined[:limit]
        ]

    return [
        {
            "vault": r.vault,
            "path": r.path,
            "title": r.title,
            "aliases": r.aliases,
            "tags": r.tags,
            "content": r.content,
        }
        for r in combined[:limit]
    ]


def path_to_note_name(path: str) -> str:
    """Convert a note path to the name used in wikilinks (filename without .md)."""
    return Path(path).stem


@mcp.tool()
async def explore(
    note_path: str,
    vault: str,
    concise: bool = False,
    ctx: Context | None = None,
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
    vaults = parse_vaults_env()
    if not vaults:
        return {"error": "No vaults configured. Set MEMEX_VAULTS env var."}

    if vault not in vaults:
        return {"error": f"Vault '{vault}' not found. Available: {list(vaults.keys())}"}

    conn = get_connection()

    async def log(msg: str) -> None:
        if ctx:
            await ctx.info(msg)

    await log(f"Checking index for vault '{vault}'...")
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

    if concise:
        return {
            "note": {"vault": note.vault, "path": note.path, "title": note.title},
            "outlinks": [{"target": t, "resolved_path": None} for t in outlink_targets],
            "backlinks": [{"path": p} for p in backlink_paths],
            "similar": [{"path": n.path, "title": n.title, "distance": round(d, 3)} for n, d in similar_notes],
        }

    return {
        "note": {
            "vault": note.vault,
            "path": note.path,
            "title": note.title,
            "aliases": note.aliases,
            "tags": note.tags,
            "content": note.content,
        },
        "outlinks": [{"target": t, "resolved_path": None} for t in outlink_targets],
        "backlinks": [{"path": p} for p in backlink_paths],
        "similar": [
            {"vault": n.vault, "path": n.path, "title": n.title, "distance": round(d, 3)}
            for n, d in similar_notes
        ],
    }


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
