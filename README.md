You like Obsidian? Your LLM will love it too.

# memex-md-mcp (WIP!)

MCP server for semantic search over markdown vaults. Hybrid FTS5 + embeddings search, wikilink graph traversal, and YAML frontmatter awareness (aliases, tags).

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

**search(query, vault?, limit=5, concise=False)** finds notes using hybrid search. Works when you don't know exact note names.

```
search("terraform state locking issues")
search("architecture decisions for the auth system", vault="work")
search("preferences for error handling in this codebase")
```

**explore(note_path, vault, concise=False)** shows a note's neighborhood: outlinks (what it references), backlinks (what references it), and semantically similar notes that aren't yet linked.

```
explore("architecture/api-design.md", "work")
```

**mcp_info()** returns this README.


TODO replace slop workflow example with my actual workflow:

<details>
<summary><h2>Example Workflow</h2></summary>

Template for integrating memex into your project's CLAUDE.md instructions:

~~~markdown
## Knowledge Base (memex MCP)

This project uses memex for persistent knowledge across sessions. The vault contains architecture decisions, debugging learnings, and context that survives agent handovers.

### When to Search

Before starting significant work, search for relevant prior knowledge:

- search("authentication patterns") - architecture decisions
- search("docker networking issues") - past debugging learnings
- search("gradient descent variants") - conceptual knowledge

The search uses both keywords AND semantic similarity, so you don't need exact note names.

If you find a relevant note, use explore() to see its neighborhood - what links to it, what it links to, and semantically similar notes.

### Handover Workflow

Plan files are your working memory that survives context exhaustion.

**During work:**
1. Create/update plan file continuously as you work
2. Capture: current state, decisions made, next steps, dead ends
3. The plan file should always reflect "where am I right now"

**When context runs low:**
1. Your plan file already has everything important (you've been updating it)
2. No panic - the next agent reads plan + searches vault and continues

**After feature completion:**
1. Distill learnings: What worked? What didn't? What should future agents know?
2. Update vault with permanent knowledge
3. Plan files can be archived or deleted - they're ephemeral

### What Goes Where

- **Current task state, decisions, next steps** → Plan file (ephemeral)
- **Code conventions, project-specific rules** → CLAUDE.md
- **Architecture decisions, patterns, tech debt** → Architecture doc in vault
- **Debugging learnings, gotchas** → Notes in vault
- **General knowledge about tools/libraries** → Notes in vault  

### Note Writing Guidelines

- **Atomic notes**: One concept per note when possible
- **Descriptive titles**: `runpod-cli-gotchas.md` not `notes-2024-12.md`
- **Update over duplicate**: Search first, update existing notes rather than creating duplicates
- **Link related notes**: Use [[wikilinks]] to connect related concepts
~~~

</details>

## Development

```bash
uv sync
make check   # ruff + ty
make test    # pytest
```
