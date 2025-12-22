# Task: Search & Explore Improvements

## Goal

Improve memex search quality and usability based on evaluation findings:
1. Fix empty query + keywords garbage results
2. Add pagination for search
3. Improve tool descriptions based on query format findings
4. Consider pagination for explore's similar notes

## Constraints / Design Decisions

- Performance should stay under 3-5 seconds per query (current: ~0.5s)
- Keep API simple - avoid over-engineering
- Semantic search remains primary; FTS is a fallback/boost mechanism

## Assumptions

- [VERIFIED] A1: Empty query with keywords produces garbage - semantic search runs on empty string embedding, pollutes RRF results
- [VERIFIED] A2: Question format queries outperform statement format significantly (9-0 in testing)
- [VERIFIED] A3: 2-sentence queries often optimal - enough context without dilution
- [VERIFIED] A4: Single keyword queries unreliable for long notes - embedding dilution
- [UNVERIFIED] A5: Fetching more similar notes (>5) won't significantly hurt explore performance
- [UNVERIFIED] A6: Current RRF k=20 is reasonable (limited testing suggests yes)

## Current State

**All main goals completed.** Ready for release.

Commits:
- `aad243c` - Fix wikilink resolution, vault ID collision, include frontmatter
- `fe0ecdf` - Log cwd and vaults on startup
- `a51e0d0` - Add optional query, pagination, and improve tool descriptions

## Completed

### Priority 1: Empty Query Fix ✓
- [x] Make `query` parameter optional (`str | None = None`)
- [x] When query is None and keywords provided: run FTS-only
- [x] Update tool description to clarify this mode

### Priority 2: Pagination for Search ✓
- [x] Add `page` parameter (default 1, 1-indexed)
- [x] Offset calculation: `offset = (page - 1) * limit`
- [x] Checked MCP docs - uses cursor-based, but page simpler for our stateless search

### Priority 3: Tool Description Improvements ✓
- [x] Emphasize 1-3 sentences as ideal query length
- [x] Recommend question format ("What...?", "How did we...?")
- [x] Realistic examples in docstring
- [x] Clarify keywords param for exact term boosting

### Priority 5: README Workflow Guidance ✓
- [x] Document the search → explore workflow pattern
- [x] Search tips in example CLAUDE.md section
- [x] Position tools as exploratory/connection-making

## Remaining (Lower Priority)

### Explore Pagination (maybe)
- [ ] Consider paging similar notes only
- [ ] Keep outlinks/backlinks complete (just paths, not expensive)
- [ ] Test performance impact of fetching more similar notes

## Resolved Questions

1. Pagination: No total count (adds complexity, marginal benefit)
2. Query optional: `str | None` (explicit > implicit)
3. v1/v2 in logs: From tests using short vault names

## Notes / Findings

### Query Format Impact (from evaluation agent)

Question format consistently beats statement format:
- "What is the transformer architecture?" (#4) vs "The transformer architecture." (#8)
- "What is backpropagation?" (#1) vs "Backpropagation." (#7)

2-sentence queries often best - provides enough context without dilution.

### Search Mode Recommendations

| Scenario | Approach |
|----------|----------|
| Know what you're looking for | 1-2 sentence question + keywords |
| Exploring concepts | 2-3 sentence description |
| Exact term lookup | FTS-only (empty query + keywords) |
| Finding related notes | Start with search, then explore() |

### Tool Philosophy

Memex tools are designed for **exploratory, connection-making** use cases:
- Find conceptually related notes
- Discover unexpected connections via wikilinks and semantic similarity
- Build context by following the graph

They are NOT optimized for precise lookup. Long notes suffer from:
- Semantic: embedding dilution (11k chars → mushy embedding)
- FTS: keyword noise (many terms → less precise matches)

**Recommended workflow:** Semantic search finds entry points → explore() follows connections → gather rich context. For precise "find this exact thing" queries, standard bash tools (grep, rg) work better.

This is a feature, not a bug - the tools encourage the kind of connected thinking that makes a knowledge base valuable.

### Performance Baseline

From logs:
- search: ~0.1-0.5s for 5 results
- explore: ~0.14-0.55s (varies with outlink/backlink count)
- Indexing 3789 notes: ~0.4s (re-index single file)

### Empty Query Problem

When `query=""` with `keywords=["transformer"]`:
1. `embed_text("")` produces meaningless embedding
2. Semantic search returns arbitrary notes
3. FTS finds correct results
4. RRF fusion mixes garbage with good results
5. Result: keywords help but don't fully override bad semantic

Solution: Skip semantic search when query is empty, run pure FTS.

---

## Work Log

### 2024-12-22 - Session Summary

**Completed:**
1. Fixed wikilink resolution - `explore()` now returns `resolved_path` for outlinks
   - Matches against note title (case-insensitive)
   - Returns multiple paths if duplicates exist
   - Returns null/empty if unresolved

2. Fixed vault ID collision - uses absolute path instead of folder name
   - Two vaults named "docs" no longer collide

3. Content now includes frontmatter
   - Aliases naturally included in embedding/FTS
   - Still parse frontmatter for structured metadata in results

4. Improved startup logging - shows cwd and vaults
   - Helps identify which Claude Code session triggered logs

5. Search results now grouped by vault path
   - Token efficient, LLM knows absolute paths for file operations

**User shared evaluation report from another agent:**
- Tested query formats across ~10 notes
- Question format wins 9-0 vs statements
- 2-sentence queries often optimal
- Empty query + keywords produces garbage (confirmed)
- Wikilink resolution now works (55 backlinks to transformer.md!)

**Files modified:**
- `src/memex_md_mcp/server.py` - vault ID, result format, wikilink in explore
- `src/memex_md_mcp/db.py` - `resolve_wikilink()`, updated `get_outlinks()`
- `src/memex_md_mcp/parser.py` - raw content instead of stripped
- `src/memex_md_mcp/logging.py` - cwd and vaults in startup log
- `tests/test_db.py` - 6 new tests for wikilink resolution

**Tests:** All 65 pass

**User feedback:**
- Pagination would be useful for search
- Empty query + keywords should do FTS-only
- Performance up to 3-5s acceptable, current ~0.5s is good
- Tool descriptions need improvement based on findings

### 2024-12-22 - Session 2: Implement Improvements

**Completed:**
1. Made `query` parameter optional (`str | None = None`)
   - When None + keywords: FTS-only mode
   - Error if both query and keywords are None

2. Added pagination with `page` parameter
   - 1-indexed, fetches `page * limit` from sources, slices appropriately
   - Checked MCP docs: they use cursor-based, but page simpler for stateless search
   - Accepted minor inconsistency if files change between page calls (pragmatic)

3. Improved tool descriptions
   - Realistic examples ("What authentication approach did we decide on?")
   - Query format tips (1-3 sentences, question format)
   - Updated README Tools section

4. Added 8 server tests
   - Query optional tests: FTS-only, error on both None, semantic only, combined
   - Pagination tests: page 1, page 2 different, beyond results, concise mode

**Files modified:**
- `src/memex_md_mcp/server.py` - optional query, pagination, improved docstrings
- `README.md` - updated Tools section with new params and examples
- `tests/test_server.py` - new file with 8 tests

**Tests:** All 73 pass
