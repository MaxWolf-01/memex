# memex-md

*You like Obsidian? Your LLM will love it too.*

*[Memex](https://en.wikipedia.org/wiki/Memex): Vannevar Bush's 1945 concept of a "memory extender" - a device for storing and retrieving personal knowledge. The conceptual ancestor of personal wikis and second brains.*

Semantic search and wikilink graph traversal for markdown vaults. Uses any [sentence-transformers](https://sbert.net/) model. Point it at your Obsidian vault (or any markdown folder).

## Quick Start

```bash
uvx memex-md --help

memex vault:add personal ~/notes ~/journal
memex search "How does the auth flow handle token refresh?" -v personal
memex explore auth personal
```

For a memex skill, see [my agent workflows](https://github.com/MaxWolf-01/agents).

## How It Works

A **vault** is a named collection of directories. Each vault has its own embedding model and SQLite index (`~/.local/share/memex-md/<vault-name>/index.db`).

The index contains:

- Embeddings for semantic similarity (default: [embeddinggemma-300m](https://huggingface.co/google/embeddinggemma-300m))
- Wikilink graph for backlink/outlink queries
- Extracted frontmatter (aliases, tags)

Indexing is incremental — on each command, only files with changed mtimes are re-indexed. Hidden directories (`.obsidian`, `.trash`, `.git`, etc.) are excluded.

**Note:** Initial indexing requires embedding computation. Example: ~3800 notes took ~7 minutes on an RTX 3070 Ti.

## Configuration

Vaults are configured via CLI. Config lives at `~/.config/memex/config.toml`.

```bash
memex vault:add personal ~/notes ~/journal
memex vault:add work ~/work-docs --model some/other-model
memex vault:list
memex vault:info personal
memex vault:remove personal --path ~/journal   # remove one path
memex vault:remove work                        # remove entire vault
```

Re-add a vault with `--model` to change its embedding model (triggers re-embedding). Use `--model none` to disable semantic search (wikilink navigation only).

## Commands

### search

```
memex search "How does the auth flow handle token refresh?" -v personal
memex search "What approaches did we consider for caching?" -v work --full
```

Embeds the query and ranks indexed notes by cosine distance. Natural language questions of a few sentences tend to work well.

| Flag | Description |
|------|-------------|
| `-v`, `--vault` | Search a specific vault (default: all) |
| `-n`, `--limit` | Max results (default: 5) |
| `-p`, `--page` | Pagination (default: 1) |
| `-f`, `--full` | Include note content (default: paths only) |

### explore

```
memex explore auth personal
memex explore docs/api-design work --full
```

Shows a note's outlinks (`[[wikilinks]]`), backlinks, and semantically similar notes.

`note_path` can be a title (`auth`) or path (`docs/auth.md`). Titles must be unique in the vault.

| Flag | Description |
|------|-------------|
| `-f`, `--full` | Include note content and metadata (default: graph only) |

### rename

```
memex rename old-name new-name personal
memex rename docs/guide manual work
```

Renames a note file and updates all `[[wikilinks]]` pointing to it. Handles path links, title links, aliases, and heading refs. Ambiguous links (multiple files share a name) are skipped with warning.

### index

```
memex index
memex index -v personal
```

Trigger indexing. Runs automatically before search/explore.

## Development

```bash
uv sync
make check          # ruff + ty
make test           # pytest
make release-patch  # 1.0.0 -> 1.0.1, tag, push
make release-minor  # 1.0.0 -> 1.1.0
make release-major  # 1.0.0 -> 2.0.0
```

