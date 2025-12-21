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

**search(query, keywords?, vault?, limit=5, concise=False)** — semantic search over vaults.

- `query`: Describe what you're looking for in natural language. Longer descriptions (1-3 sentences, even paragraphs) work better than keywords.
- `keywords`: Optional list of exact terms to boost. Notes containing these get ranked higher.

```
search("how the attention mechanism computes weighted sums of values")
search("past debugging attempts for the auth flow", keywords=["OAuth", "redirect"])
```

**explore(note_path, vault, concise=False)** — graph traversal from a note.

Returns outlinks (what it references), backlinks (what references it), and semantically similar notes not yet linked. Outlinks include image embeds (`![[image.png]]`)—use Read tool to view them.

```
explore("architecture/api-design.md", "work")
```

**mcp_info()** — returns this README.


## Workflow Integration

(From my own workflow) I recommend the following instructions per vault in your project's `CLAUDE.md`, adapted as needed:

```
<memex_mcp_instructions>
Memex MCP Instructions

You are equipped with the following knowledge-bases "vaults":
- /home/max/repos/github/MaxWolf-01/claude-global-knowledge # Your global knowledge about yourself, me, us/how we work together, general knowledge that transcends specific projects.
- /home/max/repos/obsidian/knowledge-base # My personal knowledge base in Obsidian format. Contains ~everything about me, my work, my interests, my projects, my knowledge, my life.

# TODO quickly describe what this MCP is, and when to use each vault (when to read, when to write)?
</memex_mcp_instructions>
```

In addition, the below commands heavily leverage memex to allow claude to let Claude work in a more autonomous, highly parallel/threaded (multiclauded), yet reliable way & easy to verify manner, and document its work in a scalable way:

<details>
<summary><h4>Example Workflow</h4></summary>

Template for integrating memex into your project's CLAUDE.md instructions:

~~~markdown
## Knowledge Base (memex MCP)


</details>

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
- [ ] More thorough benchmarking
- [ ] Ignore patterns?
- [ ] Include workflow examples as skills? Currently I use them as slash commands. Claude 5/6 might be autonomous enough to apply them directly, and grow a memex vault largely unsupervised.

