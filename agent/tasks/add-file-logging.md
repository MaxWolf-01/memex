# Plan: Add File-based Logging

## Goal

Log operations to file for debugging and monitoring. No changes to tool return values.

## Log Configuration

- **Path:** `~/.local/share/memex-md-mcp/memex.log` (same dir as indices)
- **Rotation:** 10MB max, single file (truncates when full)
- **Format:** `%(asctime)s %(levelname)-5s %(message)s`

## What to Log

### Indexing (in `indexer.py`)

After each vault indexed:
```
INFO  Indexed 'vault-name': +5 new, ~2 updated, -0 deleted (1247 total) in 2.34s
```

Errors during indexing:
```
ERROR Index error in 'vault-name': broken.md: Invalid YAML frontmatter
```

### Tool calls (in `server.py`)

Search:
```
INFO  search(query="...", vault=..., limit=...) -> 3 results, ~2847 chars (~712 tokens)
```

Explore:
```
INFO  explore(path="...", vault="...") -> outlinks=5, backlinks=2, similar=3, ~4521 chars (~1130 tokens)
```

Token estimate: `chars // 4` (rough approximation)

## Files to Change

- **New:** `src/memex_md_mcp/logging.py` - logger setup with rotation
- **Edit:** `src/memex_md_mcp/indexer.py` - add timing, log after indexing
- **Edit:** `src/memex_md_mcp/server.py` - log tool calls with response sizes
- **Edit:** `README.md` - mention log location

## Notes

- Use stdlib `logging` with `RotatingFileHandler` (backupCount=0)
- Ensure log directory exists before writing
- No new dependencies
