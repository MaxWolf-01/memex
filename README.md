# memex-md-mcp

MCP server for semantic search over markdown vaults.

## Installation

```bash
# Install from PyPI (when published)
uvx memex-md-mcp

# Or add to Claude Code
claude mcp add memex uvx memex-md-mcp -e OBSIDIAN_VAULTS="/path/to/vault1:/path/to/vault2"
```

## Configuration

Set vault paths via environment variable:

```bash
OBSIDIAN_VAULTS="/path/to/vault1:/path/to/vault2"
```

## Development

```bash
uv sync
uv run memex-md-mcp
```
