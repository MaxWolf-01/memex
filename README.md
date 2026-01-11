# memex-md-mcp

*You like Obsidian? Your LLM will love it too.*

*[Memex](https://en.wikipedia.org/wiki/Memex): Vannevar Bush's 1945 concept of a "memory extender" - a device for storing and retrieving personal knowledge. The conceptual ancestor of personal wikis and second brains.*

MCP server for searching and navigating markdown vaults. Point it at your Obsidian vault (or any markdown folder) and get semantic search, wikilink/backlink traversal, and note renaming with automatic link updates.

**What memex is:** A search and navigation layer over your markdown files. SQLite with FTS5 for keyword search, [embeddinggemma](https://huggingface.co/google/embeddinggemma-300m) for semantic similarity, wikilink graph for backlinks.

**What memex isn't:** An automatic memory system. It won't capture context or write notes for you. For that, check out [claude-mem](https://github.com/thedotmack/claude-mem) (automatic memory compression with hooks and summaries). Memex pairs well with workflow layers on top—see [my agent workflows](https://github.com/MaxWolf-01/agents) for an example using memex as the knowledge backend.

## Quick Start

```bash
claude mcp add memex -- uvx --from 'memex-md-mcp==1.*' memex-md-mcp
```

Then ask Claude to help configure your vaults - it has `mcp_info()` which explains everything. Or manually edit your settings (see Configuration below).

**Version note:** The above pins to the latest 1.x release for stability. For bleeding edge, use `memex-md-mcp@latest`—but watch the repo for releases, since major bumps may require deleting your index (`~/.local/share/memex-md-mcp/memex.db`).

## What This Does

Memex gives Claude read access to your markdown vaults. It creates a local index at `~/.local/share/memex-md-mcp/memex.db` and logs to `~/.local/share/memex-md-mcp/memex.log`. The index contains:

- Full-text search index (FTS5) for keyword matching
- Embeddings (google/embeddinggemma-300m) for semantic similarity
- Wikilink graph for backlink queries
- Extracted frontmatter (aliases, tags)

On each query, memex checks file mtimes and re-indexes any changed files.

**Note:** Initial indexing requires embedding computation. Example: ~3800 notes took ~7 minutes on an RTX 3070 Ti. Subsequent queries only re-index changed files and are fast.

Hidden directories (`.obsidian`, `.trash`, `.git`, etc.) are excluded from indexing.

Writing to notes happens through Claude Code's normal file tools. 

## Configuration

Add to `~/.claude/mcp.json` (global) or `.mcp.json` (per-project):

```json
{
  "mcpServers": {
    "memex": {
      "command": "uvx",
      "args": ["memex-md-mcp@latest"],
      "env": {
        "MEMEX_VAULTS": "/home/user/knowledge:/home/user/project/docs"
      }
    }
  }
}
```

Multiple vault paths are colon-separated. Project `.mcp.json` **overrides** global config entirely (no merging), so list all vaults you need.

### Optional: Disable Semantic Search

If you only need wikilink navigation and keyword search (no GPU/embeddings):

```json
"env": {
  "MEMEX_VAULTS": "...",
  "MEMEX_DISABLE_SEMANTIC": "1"
}
```

When disabled: `search()` only works with `keywords`, `explore()` returns empty `similar` list.

## Tools

**search(query?, keywords?, vault?, limit=5, page=1, concise=True)** — semantic search over vaults.

- `query`: Describe what you're looking for in natural language. Use 1-3 sentences, question format works well. If omitted, runs FTS-only mode with keywords.
- `keywords`: Optional list of exact terms to boost. Required if query is omitted.
- `page`: Page number for pagination (1-indexed).
- `concise`: Returns only paths by default. Use `concise=False` for full content.

```
search("What authentication approach did we decide on? I remember we discussed OAuth.")
search("How does the caching layer handle invalidation?", keywords=["Redis", "TTL"])
search(keywords=["PostgreSQL"])  # FTS-only mode
```

**explore(note_path, vault, concise=False)** — graph traversal from a note.

Returns outlinks (what it references), backlinks (what references it), and semantically similar notes not yet linked. Includes full content of the explored note (not neighbors). Outlinks include image embeds (`![[image.png]]`)—use Read tool to view them.

`note_path` can be a full path or just the title (if unique in vault):
```
explore("api-design", "/home/user/vault")              # by title (if unique)
explore("architecture/api-design", "/home/user/vault") # by path
```

**Typical workflow:** `search()` to find entry points → `explore()` promising results to read content + see connections.

**rename(note_path, new_name, vault)** — rename a note and update all wikilinks.

Renames the file and updates all `[[wikilinks]]` pointing to it. Handles edge cases:
- Path-based links: `[[subdir/note]]` → `[[subdir/newname]]`
- Title-based links: `[[note]]` → `[[newname]]`
- Preserves aliases/headings: `[[note#section|Display]]` → `[[newname#section|Display]]`
- Ambiguous links (multiple files share a name): skipped with warning

```
rename("old-name", "new-name", "/home/user/vault")
rename("docs/guide", "manual", "/home/user/vault")  # also updates [[docs/guide]] links
```

**mcp_info()** — returns this README.


## Workflow Integration

Add to your project's `CLAUDE.md` (adapt paths to your setup):

```markdown
# Memex MCP

You have access to markdown vaults via memex. Use them to find past work, discover connections, and document knowledge that helps future sessions.

Vaults:
- ...

Search tips:
- Use 1-3 sentence questions, not keywords: "How does the auth flow handle token refresh?" beats "auth token refresh"
- Mention key terms explicitly in your query
- For exact term lookup, use keywords parameter with a focused query
- For precise "find this exact file/string" needs, use grep/rg instead — memex is for exploration

Workflow: search() returns paths by default (concise) → explore() promising results to read content + see connections → Build context before implementation.
```

For how I use memex, see [my agent stuff](https://github.com/MaxWolf-01/agents).

## Benchmarks

Performance:

- For now mostly my own vibes, still developing a proper workflow around this.
- So far I only tested semantic and FTS search in isolation on my 3.8k note Obsidian vault to tune it.

Speed:
- Initial indexing: ~7 minutes for ~3800 notes (RTX 3070 Ti)
- Subsequent queries: ~instant

## Development

```bash
uv sync
make check          # ruff + ty
make test           # pytest
make release-patch  # 0.2.6 -> 0.2.7, tag, push
make release-minor  # 0.2.6 -> 0.3.0
make release-major  # 0.2.6 -> 1.0.0
```

