# memex-md-mcp

*You like Obsidian? Your LLM will love it too.*

*[Memex](https://en.wikipedia.org/wiki/Memex): Vannevar Bush's 1945 concept of a "memory extender" - a device for storing and retrieving personal knowledge. The conceptual ancestor of personal wikis and second brains.*

MCP server for semantic search over markdown vaults. Give your LLM persistent memory across sessions—its own knowledge base to grow, document findings, model your preferences, and recall past work.

## Quick Start

```bash
claude mcp add memex uvx memex-md-mcp@latest
```

Then ask Claude to help configure your vaults - it has `mcp_info()` which explains everything. Or manually edit your settings (see Configuration below).

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

## Tools

**search(query?, keywords?, vault?, limit=5, page=1, concise=False)** — semantic search over vaults.

- `query`: Describe what you're looking for in natural language. Use 1-3 sentences, question format works well. If omitted, runs FTS-only mode with keywords.
- `keywords`: Optional list of exact terms to boost. Required if query is omitted.
- `page`: Page number for pagination (1-indexed).

```
search("What authentication approach did we decide on? I remember we discussed OAuth.")
search("How does the caching layer handle invalidation?", keywords=["Redis", "TTL"])
search(keywords=["PostgreSQL"])  # FTS-only mode
```

**explore(note_path, vault, concise=False)** — graph traversal from a note.

Returns outlinks (what it references), backlinks (what references it), and semantically similar notes not yet linked. Outlinks include image embeds (`![[image.png]]`)—use Read tool to view them.

```
explore("architecture/api-design.md", "/home/user/project/docs")
```

**mcp_info()** — returns this README.


## Workflow Integration

Add to your project's `CLAUDE.md` (adapt paths to your setup):

```markdown
# Memex MCP

You have access to markdown vaults via memex. Use them to find past work, discover connections, and document knowledge that helps future sessions.

Vaults:
- /home/max/repos/github/MaxWolf-01/claude-global-knowledge — Your global knowledge: cross-project learnings, user preferences, workflow insights
- ./agent/knowledge — Project-specific: architecture decisions, conventions, debugging patterns

Search tips:
- Use 1-3 sentence questions, not keywords: "How does the auth flow handle token refresh?" beats "auth token refresh"
- Mention key terms explicitly in your query
- For exact term lookup, use keywords parameter with a focused query
- For precise "find this exact file/string" needs, use grep/rg instead — memex is for exploration

Workflow: Search to find entry points → Explore to follow connections (outlinks, backlinks, similar notes) → Build context before implementation.
```

For structured task management and knowledge archiving that leverage memex, see [`/task`](https://github.com/MaxWolf-01/dotfiles/blob/master/claude/commands/task.md) and [`/archive`](https://github.com/MaxWolf-01/dotfiles/blob/master/claude/commands/archive.md) — example workflows for autonomous, parallel (multi-clauded), yet reliable and verifiable work.

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
make check   # ruff + ty
make test    # pytest
make release # bumps minor version, builds and publishes to pypi with new tag
```

## Roadmap

- [x] Basic functionality
- [x] Refinement of search ranking
- [ ] Release workflow: Add/adapt make commands to release with optionally major/minor bumps & maybe once 1.0 in install instructions note that @latest can be used but then you might not know whether you need to re-index/delete the old db... can uvx handle updating only patch versions via "@..."? So yh live dangerously or use the 1.0.0 version and occasionally check or rather subscribe the repo for releases. Prlly won't change that much.
- [ ] More thorough benchmarking
- [ ] Ignore patterns?
- [ ] Include workflow examples as skills? Currently I use them as slash commands. Claude 5/6 might be autonomous enough to apply them directly, and grow a memex vault largely unsupervised. 
   - Actually, a step towards that will probably be agents managing other agents with this workflow.
   - Also see https://github.com/anthropics/claude-code/issues/13115

