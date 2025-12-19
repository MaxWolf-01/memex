# Handover Notes

Session context for continuing development.

## Interaction Style

- Max wants to be hands-on, understand the code, make decisions
- Claude explains concepts/architecture, Max approves, then Claude implements or Max codes
- Pedagogical approach - learning while building
- Push back on over-engineering, keep it simple (YAGNI)
- Small scope: target <500 lines total

## Decisions Made

### Architecture
- **Single SQLite DB** with vault column (simpler than per-vault DBs)
- **Tools only** - no MCP resources or prompts needed for this use case
- **Regex parsing** for tags/wikilinks (can swap to onyx/markdown-it-py later if edge cases bite)
- **stdio transport** - standard for Claude Code integration

### Tech Stack
- Python 3.14 (latest, why not)
- uv build system (`uv_build` backend)
- ruff (lint + format), ty (typecheck), pytest
- Deps managed via `uv add`, not manual pyproject.toml edits

### MCP Concepts Clarified
- **Tools**: LLM-controlled, for actions/queries (our `search`)
- **Resources**: App-controlled, pre-loaded context (not needed here)
- **Prompts**: User-invoked templates/slash commands (not needed here)
- **stdio warning**: Never print() to stdout - corrupts JSON-RPC. Use ctx.info() or stderr.
- **Context (`ctx`)**: Auto-injected by FastMCP when type-hinted, provides logging/progress

### Settings/Config
- MCP servers get config via env vars only (no direct settings file access)
- `OBSIDIAN_VAULTS="/path1:/path2"` - colon-separated vault paths
- Claude Code settings: `~/.claude.json` (user), `.mcp.json` (project)

## What's Done

```
src/memex_md_mcp/
├── __init__.py      ✓
├── __main__.py      ✓ (for python -m invocation)
├── server.py        ✓ (MCP server with working FTS search)
├── parser.py        ✓ (frontmatter, tags, wikilinks extraction)
├── db.py            ✓ (SQLite + FTS5, notes/wikilinks tables)
├── indexer.py       ✓ (file discovery, mtime-based staleness, incremental indexing)
```

- PyPI package claimed and published (`memex-md-mcp`)
- FTS search working end-to-end in Claude Code
- 57 pytest tests (parser, db, indexer)
- Makefile with test/check/format/build/publish targets
- pre-commit config (ruff + ty)
- Initial commit: c4f425a

## What's Next

1. ~~db.py~~ ✓
2. ~~indexer.py~~ ✓
3. **Embeddings** - add sentence-transformers, sqlite-vss, embed notes
4. **Hybrid search** - combine semantic + FTS ranking
5. **Wire it up** - lifespan for model loading

## File Structure (Target)

```
src/memex_md_mcp/
├── __init__.py
├── server.py      # MCP server, tools, lifespan
├── parser.py      # Markdown parsing ✓
├── db.py          # SQLite schema, FTS5, sqlite-vss
├── indexer.py     # File discovery, staleness, re-indexing
└── search.py      # Hybrid search logic
```

## Notes

- Max's friend is building "onyx" - a Zig markdown parser, sub-ms fast. Potential future swap.
- Index location: `~/.local/share/memex-md-mcp/indices/memex.db`
- Embedding model: `google/embeddinggemma-300m` (768-dim, ~600MB)
- Progress logging: max ~10 messages during indexing (modulo N based on file count)
