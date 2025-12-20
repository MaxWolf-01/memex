You like Obsidian? Your LLM will love it too.

# memex-md-mcp

MCP server for semantic search over markdown vaults. Hybrid FTS5 + embeddings search, wikilink graph traversal, and YAML frontmatter awareness (aliases, tags).

## Quick Start

```bash
claude mcp add memex uvx memex-md-mcp@latest
```

Then ask Claude to help configure your vaults - it has `mcp_info()` which explains everything. Or manually edit your settings (see Configuration below).

## What This Does

Memex gives Claude read access to your markdown vaults. It creates a local index at `~/.local/share/memex-md-mcp/memex.db` containing:

- Full-text search index (FTS5) for keyword matching
- Embeddings (google/embeddinggemma-300m) for semantic similarity
- Wikilink graph for backlink queries
- Extracted frontmatter (aliases, tags)

On each query, memex checks file mtimes and re-indexes any changed files. First run on a large vault takes time to compute embeddings.

Writing to notes happens through Claude Code's normal file tools. 

## Configuration

### Global vault (always available)

In `~/.claude/settings.json`:

```json
{
  "env": {
    "OBSIDIAN_VAULTS": "/home/user/obsidian/knowledge"
  },
  "mcpServers": {
    "memex": {
      "command": "uvx",
      "args": ["memex-md-mcp@latest"]
    }
  }
}
```

### Adding project-specific vaults

In your project's `.mcp.json`, use variable expansion to append to global vaults:

```json
{
  "mcpServers": {
    "memex": {
      "command": "uvx",
      "args": ["memex-md-mcp@latest"],
      "env": {
        "OBSIDIAN_VAULTS": "${OBSIDIAN_VAULTS}:/home/user/projects/myproject/docs"
      }
    }
  }
}
```

This keeps your global vault active while adding project-specific ones.

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
