# Memex Design Brainstorm

## Goal

Define the right abstractions for memex tools before implementing. Get search() and explore() right so they support Max's workflow of:
1. Finding relevant notes efficiently
2. Exploring connections around found notes
3. Enabling handover file workflows and knowledge distillation

**Shipping context:** "Shipping" means Max dogfooding for ~1 week, then actual public release. Optimize for his workflow first.

## Max's Workflow Context (from yapit CLAUDE.md)

The memex MCP needs to support this workflow:
- **Plan files** = devlogs/handover documents, updated continuously during work
- **Knowledge distillation** = after feature completion, learnings flow to appropriate places
- **Context exhaustion resilience** = handover files let next agent resume exactly where previous left off
- **Architecture docs** = medium-long term context (like yapit-architecture.md)

Key insight: Handover files should be automatic/continuous. Knowledge updates should be explicit (via prompt) after work is done.

---

## Questions to Address

### 1. FTS with Natural Language Queries

**Q:** Will FTS return garbage for natural language prompts like "I'm working on XYZ"?

**A:** FTS5 uses BM25 ranking which heavily weights rare terms and ignores stopwords. "I'm working on XYZ feature" would match mostly on "XYZ" and "feature". But FTS is fundamentally keyword-based - it won't understand intent.

**Current hybrid approach:** FTS for exact keyword matches + semantic for conceptual matches. The semantic model handles intent; FTS handles precision when keywords match.

**Concern:** If FTS returns noise and semantic returns good results, does our current dedup (FTS first, then semantic) hurt ranking?

### 2. What Happens When FTS Finds Nothing But Semantic Does?

**Q:** If FTS returns 0 results but semantic returns good matches, will they surface in top 3?

**A (current behavior):** Yes. We iterate FTS results first, then semantic. If FTS returns nothing, all top results come from semantic search. The dedup is by (vault, path) key, not by score. So semantic results absolutely surface when FTS fails.

**Potential issue:** If FTS returns 2 mediocre matches and semantic returns 3 great matches, our current logic puts FTS first. We're not doing reciprocal rank fusion or score-based merging.

**Proposed fix:** Consider semantic-first ordering, or proper RRF. But this is tuning - the basic functionality works.

### 3. Wikilinks in Results

**Current state:**
- Wikilinks are **indexed** (stored in wikilinks table)
- Wikilinks are **NOT returned** in search results
- The note content contains the raw `[[link]]` syntax
- We have `get_backlinks()` function but no tool exposes it

**What Max wants:**
- search() → find notes (no graph exploration)
- explore() → show outlinks, backlinks, and semantically similar unlinked notes

### 4. Semantic Search as "Intuition"

Max's insight: Fresh agents don't know all the keywords or node names. Semantic search provides "intuitive recall" - finding conceptually related notes even without exact terminology.

This is exactly what we built. The "context manager python" test case proves it works - finds notes about `contextlib.contextmanager` without using those keywords.

### 5. Search Result Count and Precision

**Max's preference:** Return 3-5 notes, not 20. Precision over recall for search().

**Current:** `limit` parameter defaults to 10.

**Proposed:** Default to 5. Maybe even 3 for high-precision use cases.

**Threshold question:** Should we filter by semantic distance threshold? e.g., only return notes within 0.7 cosine distance?

Pros: Avoids returning irrelevant results when query is novel
Cons: Might filter out useful results; threshold tuning is fiddly

**Suggestion:** Start without threshold, tune based on real usage. The limit=5 default is probably sufficient.

### 6. Two Tools: search() and explore()

**search(query, vault?, limit=5)**
- Finds notes matching query
- Hybrid FTS + semantic
- Returns: vault, path, title, aliases, tags, content
- Focus: precision, find the right note(s)

**explore(note_path, vault)**
- Shows neighborhood of a specific note
- Returns:
  - outlinks: notes this note links to (resolved wikilinks)
  - backlinks: notes that link to this note
  - similar: top 3-5 semantically similar notes NOT already linked
- Focus: discovery, understanding connections

**Why separate:**
- Different purposes (find vs explore)
- search() might return 5 notes; auto-fetching all their links = explosion of context
- Agent decides when to explore: "I found the main note, let me see what's connected"

### 7. Auto-Fetching Degree-1 Links in Search?

**Max asked:** Should search() instantly fetch all wikilinked notes?

**Against:**
- 5 results × 10 links each = 50 notes of context
- Violates single-purpose principle
- Agent loses control over context usage

**For:**
- One tool call instead of two
- "Fat" results might be more useful

**Decision:** Keep search() lean. Let agent call explore() when needed. This is composable.

### 8. MCP Prompts for Workflow

**Idea:** Ship prompts with the MCP for:
- `/update-knowledge` - distill learnings after feature completion
- `/handover` - format current state for next agent (though this might be instructions, not a prompt)

**Note:** MCP prompts don't consume tokens until invoked. They're essentially slash commands shipped with the tool.

**Status:** Nice-to-have for v1. Focus on tools first.

### 9. Tags as Organization

**Current:** Tags are extracted and indexed, searchable via FTS.

**Max's idea:** Search for notes with specific tag (e.g., "architecture").

**Options:**
a) Add `tag` parameter to search(): `search(query, tag="architecture")`
b) Separate tool: `notes_by_tag(tag, vault?)`
c) Just use search: `search("#architecture")` (FTS would match)

**Recommendation:** Start with (c) - FTS already indexes tags. If it's not precise enough, add (a) later.

---

## Technical Questions to Investigate

1. **Wikilink resolution:** Currently we store `target_raw` (the raw `[[target]]` text). Do we resolve to actual file paths? What if target doesn't exist?

2. **Outlinks extraction:** We parse and store wikilinks during indexing. Need to verify they're correctly associated with source notes.

3. **Semantic similarity for explore():** Need to query for similar notes excluding those already linked. This requires:
   - Get embeddings for the target note
   - Search semantic with that embedding
   - Filter out notes that are in outlinks or backlinks

---

## Proposed Implementation Order

1. **Tune search() defaults:**
   - [ ] Change default limit from 10 to 5
   - [ ] Consider semantic-first ordering in hybrid results

2. **Implement explore():**
   - [ ] Add `get_outlinks(vault, path)` to db.py
   - [ ] Add `explore()` tool to server.py
   - [ ] Include similar-but-unlinked notes

3. **Improve MCP description:**
   - [ ] Rewrite instructions to reflect search/explore distinction
   - [ ] Better examples (not "python async patterns" - that's what docs are for)
   - [ ] Examples: "find architecture decisions", "what do we know about X"

4. **README:**
   - [ ] Installation (uvx, pip)
   - [ ] Configuration (OBSIDIAN_VAULTS env var)
   - [ ] Basic usage examples

5. **Skip for now:**
   - list_notes() - anti-pattern
   - MCP prompts - nice-to-have
   - Threshold tuning - wait for real usage data

---

## Open Questions for Max

1. **Wikilink resolution:** If `[[some-note]]` doesn't match any actual file, should we:
   a) Return it anyway (with null path)?
   b) Omit it?
   c) Try fuzzy matching to find likely target?

2. **explore() output format:** Should similar notes include a "similarity reason" or just the note content?

3. **Search ranking:** Should we try semantic-first ordering now, or ship current (FTS-first) and iterate?

4. **Examples for MCP description:** What are 2-3 real queries you'd use? These will be the examples in the tool description.

---

## Anthropic Blog: Writing Tools for Agents

Key principles from https://www.anthropic.com/engineering/writing-tools-for-agents:

1. **Design for agent workflows, not API wrappers**
   - Bad: `list_contacts` (forces token-by-token reading)
   - Good: `search_contacts` (targets specific need)
   - Our search() and explore() follow this - specific workflows, not generic CRUD

2. **Consolidate related operations**
   - Single tool for complete workflow
   - explore() does this: outlinks + backlinks + similar in one call

3. **Clear parameter names**
   - `user_id` not `user`
   - We have: `query`, `vault`, `note_path` - all clear

4. **Explicit descriptions**
   - "Like explaining to a new hire - make implicit knowledge explicit"
   - Critical: describe WHEN to use search vs explore, not just what they do

5. **Meaningful return values**
   - Semantic fields (title, content) not UUIDs
   - We do this already

6. **Response format control**
   - Consider: `concise` parameter for search?
   - Returns just path/title instead of full content
   - Agent can then Read specific notes if needed

7. **Actionable errors**
   - "No results found" should suggest: try different keywords, check vault config, etc.

---

## Decisions Made

1. **Wikilink resolution:** Return with null resolved path if target doesn't exist.
   - Mention in tool description: "null path means the note is referenced but not yet created"
   - Shows LLM the link was intentional, not hallucinated

2. **Limit default:** 5 is the starting point.
   - Reasoning: If you know exact name, you don't need search - just read the path
   - Search is for when you don't know exact terminology, or there's duplication
   - 5 gives enough options to catch variants without flooding context
   - Can tune down to 3 if too noisy in practice

3. **Ranking:** Ship FTS-first for now, tune with real usage data.
   - RRF would be ideal but adds complexity
   - Current behavior works: if FTS returns nothing, semantic surfaces

4. **Concise mode:** Add `concise=False` parameter to BOTH search() and explore()

   **search(concise=False):**
   - Default: full content (current behavior)
   - concise=True: returns vault + relative path + title only

   **explore(concise=False):**
   - Default: full content of linked notes
   - concise=True: just paths/titles for outlinks, backlinks, similar

   Agent can then Read specific notes if needed.
   Path format: `vault_name/relative/path.md` (not full absolute paths)

5. **list_notes is anti-pattern:** Explicitly called out in Anthropic blog - forces agents to read through all entries token-by-token. Skip.

6. **Monitoring/benchmarking (future):**
   - Track: token usage, calls, options used, prompts
   - Config/env var to toggle monitoring in real usage
   - Useful for tuning and for curious users

---

## Scoring Clarification (for RRF later)

**FTS5 BM25:** Higher = better match. Scale varies by corpus but typically 0-25.

**Semantic cosine distance:**
- 0.0 = identical vectors
- 1.0 = orthogonal (unrelated)
- 2.0 = opposite (rare with real text)
- Good matches typically < 0.5

**RRF sidesteps scale differences** by using rank positions:
```
score(doc) = Σ 1/(k + rank_i)
```
A doc ranked #1 in FTS and #3 in semantic gets boosted. Doesn't matter what the raw scores were.

---

## Deliverables

### 1. MCP Tools (the code)
- `search(query, vault?, limit=5)` - find notes
- `explore(note_path, vault)` - show neighborhood
- `get_mcp_instructions()` - setup/usage help (already exists, needs improvement)

### 2. Example CLAUDE.md Workflow (documentation)
A markdown section showing:
- What memex is and how to use it
- search vs explore distinction
- Handover file workflow integration
- Reference to knowledge-update prompt

This lives in the memex repo as example workflow, Max tunes it for his projects.

### 3. Knowledge-Update Prompt (future)
MCP prompt or skill for "distill learnings into vault"
- Invoked explicitly after feature completion
- Contains instructions for writing effective notes
- Max will write/tune this with prompting guidelines

---

## Example Queries for Tool Description

General examples (not project-specific):

**search() examples:**
- `search("gradient descent optimization")` - find notes on a concept with multiple names/aspects
- `search("docker networking issues")` - find debugging notes from past problems
- `search("API authentication patterns")` - find architecture decisions
- `search("null space linear algebra")` - concept that might be filed under "kernel" (alias)

**explore() examples:**
- `explore("linear-algebra-fundamentals.md", "math")` - see what concepts link to/from this
- After finding a note: explore to discover related concepts you might have forgotten

**When to use which:**
- search: "I need information about X" (don't know exact note names)
- explore: "I found note Y, what's connected to it?" (have a specific note, want context)

---

## Notes

- Max confirmed: skip list_notes() for now
- Max confirmed: always hybrid search, no mode parameter
- Max confirmed: wikilinks not necessarily human-curated (LLMs create them too), but still higher signal than pure embeddings
- Handover workflow is key use case: agent reads handover → works → updates handover → context exhausts → next agent resumes
- MCP prompts: nice-to-have, Max will write with my assistance later

---

## Current Implementation State (session handover)

**Completed:**
- Added `get_outlinks()` to db.py
- Added `get_note_embedding()` to db.py
- Updated `get_backlinks()` docstring to clarify it takes note_name not path
- Added `explore()` tool to server.py (basic implementation)
- Created `examples/claude-md-workflow.md` template

**Needs fixing (Max feedback):**
1. **Concise mode not implemented** - need to add `concise=False` param to both search() and explore()
2. **Remove superfluous inline comments** - the "Get outlinks" and "Get backlinks" comments in explore() are redundant
3. **explore() docstring too sparse** - should explain the mechanism: semantic similarity + wikilinks, what outlinks/backlinks/similar mean
4. **Cross-vault behavior:**
   - search() should work without vault param (searches all) ✓ already works
   - explore() requires vault (makes sense - exploring specific note)
5. **path_to_note_name helper** - Max questioned if useful, but it is: converts "concepts/kernel.md" → "kernel" for backlink matching
6. **Run make check** - has errors/warnings to fix
7. **Update search() limit default** from 10 to 5

**Files modified this session:**
- `src/memex_md_mcp/db.py` - added get_outlinks, get_note_embedding, updated get_backlinks
- `src/memex_md_mcp/server.py` - added explore() tool, updated imports
- `plans/memex-design-brainstorm.md` - this file
- `examples/claude-md-workflow.md` - new file

**Next steps:**
1. Run `make check` and fix any lint errors
2. Add concise parameter to search() and explore()
3. Improve explore() docstring
4. Remove redundant inline comments
5. Update search() limit default to 5
6. Write README
7. Update get_mcp_instructions() to load files dynamically
