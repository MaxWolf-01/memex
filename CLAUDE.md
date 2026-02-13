# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Always read the README.md first.

## Commands

```bash
make check                 # lint (ruff) + typecheck (ty)
make test                  # pytest
make fix                   # auto-fix lint issues
uv run pytest tests/test_parser.py -k "test_name"  # single test
```

## Architecture

CLI providing semantic search over markdown vaults (Obsidian-style). Runs as `uvx memex-md` or `memex` if installed. Vaults configured via `memex vault:add`.

**Data flow:** `cli.py` (tyro subcommands + business logic) → `indexer.py` (discovers files, orchestrates) → `parser.py` (extracts frontmatter/tags/wikilinks) + `embeddings.py` (sentence-transformers) → `db.py` (SQLite + sqlite-vec)

**Key modules:**
- `cli.py`: Tyro CLI with `search` (semantic), `explore` (graph traversal), `rename` (with link updates), `index`, `vault:*` management. Business logic (`do_search`, `do_explore`, `do_rename`) separated from CLI layer.
- `config.py`: TOML config at `~/.config/memex/config.toml`. Vaults are named groups of directories with optional per-vault embedding model.
- `db.py`: SQLite schema with notes table, wikilinks graph, vec0 virtual table for embeddings, metadata table for model tracking. One DB per vault at `~/.local/share/memex-md/<vault>/index.db`.
- `indexer.py`: Incremental indexing based on file mtime. Indexes multiple root directories per vault.
- `parser.py`: Regex-based extraction of wikilinks `[[target]]` and tags `#tag`, plus YAML frontmatter.
- `embeddings.py`: Configurable model (default google/embeddinggemma-300m), lazy-loaded singleton.

**Storage:** `~/.local/share/memex-md/<vault-name>/index.db` (per-vault index), `memex.log` (rotating log)
