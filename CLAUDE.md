# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source .venv/bin/activate  # required before running python commands
make check                 # lint (ruff) + typecheck (ty)
make test                  # pytest
make fix                   # auto-fix lint issues
uv run pytest tests/test_parser.py -k "test_name"  # single test
```

## Architecture

MCP server providing semantic search over markdown vaults (Obsidian-style). Runs as `uvx memex-md-mcp` with `MEMEX_VAULTS` env var pointing to colon-separated vault paths.

**Data flow:** `server.py` (MCP tools) → `indexer.py` (discovers files, orchestrates) → `parser.py` (extracts frontmatter/tags/wikilinks) + `embeddings.py` (sentence-transformers) → `db.py` (SQLite + FTS5 + sqlite-vec)

**Key modules:**
- `server.py`: FastMCP server with `search()` (semantic + optional FTS fusion), `explore()` (graph traversal), `mcp_info()` tools
- `db.py`: SQLite schema with notes table, FTS5 virtual table, wikilinks graph, and vec0 virtual table for embeddings. Uses RRF fusion for combined ranking.
- `indexer.py`: Incremental indexing based on file mtime
- `parser.py`: Regex-based extraction of wikilinks `[[target]]` and tags `#tag`, plus YAML frontmatter
- `embeddings.py`: google/embeddinggemma-300m, 768-dim, lazy-loaded singleton

**Storage:** `~/.local/share/memex-md-mcp/memex.db` (index), `memex.log` (rotating log)

## Knowledge Files

`agent/knowledge/` contains MCP reference docs for development:
- `mcp-llms-full.txt` - MCP protocol spec
- `mcp-python-sdk.md` - Python SDK docs
