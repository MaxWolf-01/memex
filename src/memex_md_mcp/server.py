"""MCP server for semantic search over markdown vaults."""

import os
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from memex_md_mcp.db import get_connection, search_fts
from memex_md_mcp.indexer import index_all_vaults

mcp = FastMCP(
    name="memex",
    instructions="Semantic search over markdown vaults. Use the search tool to find relevant notes.",
)


def parse_vaults_env() -> dict[str, Path]:
    """Parse OBSIDIAN_VAULTS env var into {vault_id: path} dict."""
    vaults_env = os.environ.get("OBSIDIAN_VAULTS", "")
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
async def search(query: str, vault: str | None = None, limit: int = 10, ctx: Context | None = None) -> list[dict]:
    """Search across markdown vaults using semantic + full-text search.

    Args:
        query: Natural language search query
        vault: Specific vault to search (None = all vaults)
        limit: Maximum number of results to return
    """
    vaults = parse_vaults_env()

    if not vaults:
        return [{"error": "No vaults configured. Set OBSIDIAN_VAULTS env var."}]

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

    results = search_fts(conn, query, vault=vault, limit=limit)
    conn.close()

    if not results:
        return [{"message": f"No results for '{query}'", "vaults_searched": list(vaults.keys())}]

    return [
        {
            "vault": r.vault,
            "path": r.path,
            "title": r.title,
            "aliases": r.aliases,
            "tags": r.tags,
            "content": r.content,
        }
        for r in results
    ]


@mcp.tool()
def get_mcp_instructions() -> str:
    """Get instructions for using this MCP server."""
    return """
# memex

Semantic search over markdown vaults (like Obsidian).

## Configuration

Set the OBSIDIAN_VAULTS environment variable with colon-separated paths:
  OBSIDIAN_VAULTS="/path/to/vault1:/path/to/vault2"

## Tools

- search(query, vault?, limit?) - Search across vaults
- get_mcp_instructions() - Show this help

## Example

Search for notes about "python async patterns":
  search("python async patterns")

Search in a specific vault:
  search("git workflow", vault="work")
""".strip()


def main() -> None:
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
