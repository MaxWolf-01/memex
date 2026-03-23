# memex-md

*You like Obsidian? Your LLM will love it too.*

*[Memex](https://en.wikipedia.org/wiki/Memex): Vannevar Bush's 1945 concept of a "memory extender" - a device for storing and retrieving personal knowledge. The conceptual ancestor of personal wikis and second brains.*

Semantic search and wikilink graph traversal for markdown vaults. Uses any [sentence-transformers](https://sbert.net/) model. Point it at your Obsidian vault (or any markdown folder).

## Quick Start

```bash
uvx memex-md --help
```
or
```bash
uv tool install memex-md
```

If installed, you can run `memex` directly, or via the alias `mx`:
```
mx vault:add personal ~/notes ~/journal
mx search "How does the auth flow handle token refresh?" -v personal
mx find knn
mx explore auth personal
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
mx vault:add personal ~/notes ~/journal
mx vault:add work ~/work-docs --model some/other-model
mx vault:list
mx vault:info personal
mx vault:remove personal --path ~/journal   # remove one path
mx vault:remove work                        # remove entire vault
```

Re-add a vault with `--model` to change its embedding model (triggers re-embedding). Use `--model none` to disable semantic search (wikilink navigation only).

## Commands

### find

```
mx find knn
mx find "neural ordinary" -v personal
mx find auth -n 20
```

Fuzzy match against note titles, frontmatter aliases, and paths. No embeddings needed — instant results. Ranked: exact title/alias > substring > fuzzy ([rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) WRatio). Multi-word queries match any part; more parts hitting = ranked higher.

| Flag | Description |
|------|-------------|
| `-v`, `--vault` | Search a specific vault (default: all) |
| `-n`, `--limit` | Max results (default: 10) |

### search

```
mx search "How does the auth flow handle token refresh?" -v personal
mx search "What approaches did we consider for caching?" -v work --full
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
mx explore auth personal
mx explore docs/api-design work --full
```

Shows a note's outlinks (`[[wikilinks]]`), backlinks, and semantically similar notes.

`note_path` can be a title (`auth`) or path (`docs/auth.md`). Titles must be unique in the vault.

| Flag | Description |
|------|-------------|
| `-f`, `--full` | Include note content and metadata (default: graph only) |

### rename

```
mx rename old-name new-name personal
mx rename docs/guide manual work
```

Renames a note file and updates all `[[wikilinks]]` pointing to it. Handles path links, title links, aliases, and heading refs. Ambiguous links (multiple files share a name) are skipped with warning.

### index

```
mx index
mx index -v personal
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

