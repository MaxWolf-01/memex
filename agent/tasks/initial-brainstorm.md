# memex-md-mcp: Specification

**Name**: `memex-md-mcp`
**Domain**: `memex.md` (if needed)

*Memex: Vannevar Bush's 1945 concept of a "memory extender" - a device for storing and retrieving personal knowledge. The conceptual ancestor of personal wikis and second brains.*

READ THIS FOR THE LATEST BRAINSTORM: plans/memex-design-brainstorm.md

When updating README's "Workflow Integration" section, check `agent/tasks/search-improvements.md` for query format findings and search mode recommendations to include.

## Vision & Philosophy

### The Core Idea

This MCP gives LLMs a "second brain" - the same way Obsidian serves humans. The value isn't replicating Wikipedia (LLMs already have that in training). The value is **bespoke knowledge**:

- User preferences, workflows, common pitfalls
- Project-specific context, architecture decisions
- Machine/environment details
- Research findings, explorations, experiments
- The user's trajectory and stepping stones

This enables **persistent memory across conversations** without loading everything into context.

### Core Capability: Multiple Vaults

LLMs can have their own vaults alongside user vaults. Typical setup:
- **Global knowledge vault**: Persistent knowledge about user, workflows, preferences
- **Project-specific vaults**: Plans, architecture, task tracking for specific projects

The separation isn't "use MCP vs don't use MCP" - it's between vaults. Project vaults can include execution/status (plans, todos) while global vault stays knowledge-focused.

### Medium-term Vision

- LLMs autonomously growing knowledge bases
- More autonomous, long-running agents
- Agents that work independently and report back with discoveries

### Long-term Vision (from Max)

- Connecting to **remote vaults** - reading from others' knowledge bases
- Networking together different niches of knowledge
- A new kind of "social media for knowledge" where:
  - Users/LLMs share vaults
  - Quality vaults rise via usage/ratings
  - Cross-pollination between isolated knowledge bases
  - Agents explore topics, build vaults, and periodically report back with what they learned

---

## Decided

### Core Functionality

- **Semantic search**: Embed notes, query by similarity
- **Full-text search (FTS5)**: Keyword/phrase matching with ranking
- **Integrated search**: Single tool combining semantic + FTS + aliases + tags
- **Wikilink awareness**: Index `[[links]]`, enable backlink queries
- **Alias support**: YAML frontmatter `aliases` field included in search matching
- **Tag extraction**: `#tags` indexed and searchable

### Embedding Model

- **google/embeddinggemma-300m** via sentence-transformers
- Chosen for quality over speed (user preference)
- ~600MB model download on first run
- Whole-note embedding (notes are short, atomic)

### Storage

- **sqlite + sqlite-vss**: Single DB file per vault
- **Index location**: `~/.local/share/memex-md-mcp/indices/<slugified-vault-path>.db`
- Indices are derived data (regeneratable from vault)
- sqlite-vss has binary wheels for Linux x86_64 and macOS (no Windows, that's fine)

### Configuration

MCP config lives in `~/.claude/mcp.json` (global) and `.mcp.json` (per-project). Project config **overrides** global for servers with the same name (no merging).

Vault paths via `MEMEX_VAULTS` env var (colon-separated):

```json
{
  "mcpServers": {
    "memex": {
      "command": "uvx",
      "args": ["memex-md-mcp"],
      "env": {
        "MEMEX_VAULTS": "/home/user/obsidian/personal:/home/user/obsidian/work"
      }
    }
  }
}
```

For project-specific vaults, you must list all paths (global + project) since configs don't merge.

### Permissions & Write Operations

- **MCP is read-only for vault files**: Only reads (for indexing), writes only to its own index DB
- **Note creation/editing**: Handled by Claude Code's normal Write tool with its permission system
- The MCP provides paths in search results; Claude Code handles the actual file operations
- This is not our concern - Claude Code's permission harness manages it

### Distribution

- PyPI package: `memex-md-mcp`
- Install: `pip install memex-md-mcp` or via `uvx memex-md-mcp`
- Add to Claude Code: `claude mcp add memex uvx memex-md-mcp`

### Indexing Strategy

- On MCP startup / first tool call: check file mtimes vs indexed mtimes
- Stale files: re-embed changed files
- No file watcher daemon (simplicity, no lost functionality - checking on-demand is fast enough)
- **Progress logging**: Show progress during indexing, but don't litter context. Max ~10 log messages (modulo N based on file count)

### Wikilink Parsing

`[[target|display text]]` or `[[target#heading^block|display]]`:
- Extract `target` (before `|` if present)
- Display text is irrelevant for search/linking
- Handle `#heading` and `^block-ref` in the target
- Aliases (YAML frontmatter) are a separate concept from display text

### Result Format

- **vault** + **relative path** (not full absolute path - token efficiency)
- Full note content for top results
- "More results available" hint for additional matches
- Score only for borderline results (maybe)

---

## Undecided / To Discuss

### Search Tool Interface

Single `search` tool is decided. Parameters TBD:

```python
@mcp.tool()
def search(
    query: str,
    # Potential parameters - not decided:
    vault: str | None = None,  # Scope to specific vault?
    mode: str = "hybrid",      # "semantic" | "text" | "hybrid"?
    limit: int = 10,           # How many results?
    threshold: float = 0.5,    # Minimum relevance score?
) -> SearchResults:
    ...
```

**Questions:**
- Is `mode` parameter useful, or just always do hybrid?
- What's a sensible default `limit`?
- Should threshold be exposed or hardcoded?

### Result Ranking

How to blend semantic + FTS results? Options:
- Reciprocal rank fusion
- Score normalization and weighted sum
- Semantic first, FTS as fallback

**Question for Max**: Any intuition on what would work well?

### Role of Tags

Extracted and indexed, but unclear how useful for LLM search:
- Semantic search already finds conceptually related notes
- Tags might be useful as explicit scope filter
- Or just as metadata in results

**Suggestion**: Index them, don't overthink initially. See if useful in practice.

### Additional Tools (maybe)

**Configuration/help tool** (Max's idea):
```python
@mcp.tool()
def get_mcp_instructions() -> str:
    """On-demand detailed instructions for users chatting with Claude about how to use/configure the MCP."""
```

Good UX for setup - user asks "how do I use this?" and Claude can explain capabilities and workflows.

**Backlinks tool** (maybe):
```python
@mcp.tool()
def backlinks(note_path: str) -> list[str]:
    """Get all notes that link TO this note."""
```

Or: include backlinks in search results / note retrieval. TBD.

**List notes tool** (for discoverability):
```python
@mcp.tool()
def list_notes(vault: str | None = None) -> list[str]:
    """List all indexed note titles. For browsing, not searching."""
```

---

## Schema Design

### SQLite Tables

```sql
-- Note metadata and content
CREATE TABLE notes (
    path TEXT PRIMARY KEY,      -- relative path within vault
    vault TEXT NOT NULL,        -- vault identifier (slugified path)
    title TEXT,                 -- filename without extension
    aliases TEXT,               -- JSON array from frontmatter
    tags TEXT,                  -- JSON array of #tags found in content
    content TEXT,               -- full note content
    mtime REAL,                 -- file modification time
    content_hash TEXT           -- for change detection
);

-- Full-text search (FTS5)
CREATE VIRTUAL TABLE notes_fts USING fts5(
    title, aliases, tags, content,
    content='notes',
    content_rowid='rowid'
);

-- Vector embeddings (sqlite-vss)
CREATE VIRTUAL TABLE notes_vss USING vss0(
    embedding(768)  -- embeddinggemma-300m dimension
);

-- Wikilink graph
CREATE TABLE wikilinks (
    source_path TEXT NOT NULL,
    source_vault TEXT NOT NULL,
    target_raw TEXT NOT NULL,   -- raw target from [[target|...]], before resolution
    target_path TEXT,           -- resolved path (NULL if unresolved)
    FOREIGN KEY (source_path, source_vault) REFERENCES notes(path, vault)
);

CREATE INDEX idx_wikilinks_target ON wikilinks(target_path);
CREATE INDEX idx_wikilinks_source ON wikilinks(source_path);
```

**Why index on target/source**: Makes backlink queries (`find all notes linking TO X`) and outlink queries (`find all notes X links TO`) fast. Without indices, these scan the whole table.

**Why store target_raw**: For debugging/display when links don't resolve.

---

## Context for Future Prompting (Max's Instructions)

*These are insights from our discussion, not MCP implementation details. For when Max writes his Claude instructions on how to use the knowledge base.*

### Note Structure Suggestions

- **Atomic notes**: One concept per note. Better embedding granularity.
- **Confidence levels**: Delineate uncertainty **inline**, not just at note level. Inline markers for "I'm not sure about this part" are more useful than whole-note confidence metadata.
- **Metadata ideas** (use sparingly - less structure is probably better):
  ```yaml
  ---
  source: conversation | observed | documentation | inference
  created: 2024-12-18
  last_modified: 2024-12-18
  ---
  ```
  *Note: Confidence as frontmatter metadata is probably less useful than inline delineation. TBD.*
- **Changelog at bottom**: What got added/changed when. Adds friction but provides staleness insight.

### What Might Work Well

- Short, focused notes (better embeddings than verbose rambling)
- Descriptive filenames (`workflow-git-conventions.md` > `notes-2024-12.md`)
- Update existing notes rather than creating duplicates on same topic
- Separation: knowledge vs execution/status

### What Might NOT Work Well

- Verbose notes → mushy embeddings
- Over-templating → friction → won't use
- Auto-generating everything → slop accumulates
- Mixing temporal ("today I learned") with permanent ("X works like Y")

### Instruction Integration (for Claude instructions, not MCP)

Options from simplest to complex:
1. **Just prompting**: "After significant discoveries, consider adding to vault"
2. **Custom command**: `/remember` or `/reflect` for explicit trigger
3. **Hook on compaction**: Auto-prompt before context compacts

**Suggestion**: Start with (1), see if it works.

---

## Open Questions (for Max to consider)

1. **Granularity**: One note per concept? Or grouped like `python-patterns.md`?

2. **Updates vs history**: When knowledge changes, overwrite? Or keep history?

3. **Confidence decay**: 6-month-old knowledge might be stale. Any mechanism?

4. **Discoverability**: Beyond search, way to browse what's in the vault?

5. **Multi-vault semantics**: Personal vault vs LLM-generated vault? Or mixed?

---

## Implementation Notes

### Dependencies

```
mcp[cli]
sentence-transformers
sqlite-vss
python-frontmatter
```

### Regex for Wikilinks

```python
# Matches [[target]], [[target|display]], [[target#heading]], [[target#heading^block|display]]
WIKILINK_PATTERN = r'\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]'
```

Extract group 1 = target (the note to link to).

### Regex for Tags

```python
# Matches #tag, #tag/subtag, but not in code blocks
TAG_PATTERN = r'(?<!\S)#([\w/-]+)'
```

Need to be careful about code blocks - might need to strip those first or use a smarter approach.

---

## Benchmarking & Testing

### Current Test Vault Observations (very first commit)

From manual testing with the minimal `test-vault`:
- **FTS works** for exact keywords (`dataclass` → finds python-patterns.md)
- **Alias matching works** (`model context protocol` → finds mcp-notes.md via alias)
- **Semantic gap identified**: `context manager python` returns no results, even though python-patterns.md contains `contextlib.contextmanager`. FTS can't match because it's one compound word in code, not two separate words. This is a perfect test case for semantic search.

### Semantic Search Test Cases

The "context manager" case is exactly what semantic search should solve - conceptual similarity despite lexical mismatch. More cases to consider:

- **Synonyms**: "error handling" should find notes about "exception handling"
- **Conceptual queries**: "how to make code reusable" should find notes about patterns, decorators, abstractions
- **Jargon variations**: "async" vs "asynchronous", "fn" vs "function"
- **Intent-based**: "avoid code duplication" should find DRY patterns, abstractions

### Benchmarking Strategy (for public release)

**Manual smoke tests** (current):
- Verify basic FTS, alias, tag matching
- Spot-check semantic results make sense

**Automated benchmark suite** (future):
- Create a more comprehensive test vault with known-good queries
- Query → expected notes mapping (ground truth)
- Measure: precision@k, recall, semantic vs FTS comparison

**Potential setup with Claude Code in the loop**:
1. Generate a diverse test vault programmatically (or have Claude write varied notes on different topics)
2. Define test queries with expected results (some FTS-friendly, some requiring semantic understanding)
3. Run queries, compare against ground truth
4. Track regressions when changing ranking/embedding

See:
- https://www.anthropic.com/engineering/writing-tools-for-agents



### What to Test

**Basic functionality**:
- FTS exact match
- FTS partial/fuzzy match
- Alias resolution
- Tag filtering
- Wikilink indexing (backlinks work)

**Semantic search edge cases**:
- Synonym matching (the "context manager" case)
- Conceptual similarity without keyword overlap
- Multi-word concepts vs compound words
- Cross-language patterns (Python idiom described, Rust equivalent found)

**Integration**:
- Claude understanding MCP tool descriptions
- Claude formulating effective queries
- Claude interpreting results correctly
- Multi-vault scoping works as expected

**Stress/scale** (later):
- Large vaults (1000+ notes)
- Indexing performance
- Query latency

**Harder to benchmark (dogfood territory)**:
- Hierarchical/graph relationships: Does following wikilinks help Claude build context?
- Long-range dependencies: Knowledge spread across multiple notes, needs synthesis
- Emergent vault structure: How well does search work as vault grows organically?
- Real-world query patterns: What does Claude actually search for in practice?

These are probably best validated through actual usage rather than synthetic benchmarks. The automated suite catches regressions in core mechanics; real value shows up when dogfooding.

---

## What's NOT in Scope

- Write operations to vault files (Claude Code handles this)
- Commands/hooks for "remember to update" (user workflow, not MCP)
- Detailed prompting instructions (user-customizable)
- Windows support
- File watching daemon


## Context for use case / how the idea came to be / ongoing thought dump

The below is context for thinking about how to USE the MCP.
Might be relevant when thinking abt how to design the tools...

- claude idea: instead of treating plans as plans treat them as a global knowledge base
 - -> Where does the index live? Global CLAUDE.md? Rebuild index in every project to have only relevenat context? Nah. Global index, but (periodically; hooks?) pull in information (lines) into project CLAUDE.md? 
 - -> disable auto-compaction, but write a hook on "low" context (actually s.t. like 40-60% is still left) to update global knowledge? Sth like that. And also a hook to like yh do it at the end again, or like write a command for it /update-knowledge
 - -> Needs more thought about how to structure and update and describe lines in the index, how to name files, and whatever. Prlly the naming shouldn't carry too much weight, but have some prefix at least to indicate it's part of the global knowledge vs just a "normal" plan / plan that was created without thinking of it as being global knowledge!
 - -> Then actual PLANS will also work better than the system I have currently, if knowledge is separated.
	  -  ==it's extremely important that claude distinguishes between what he know for a FACT, is pretty confident, suspects, sees as a possibility or is speculating when writing KNOWLEDGE files==
	  - this especially applies for (my) "opinions/preferences" or "decisions we made" - as he often is very happy to overconfidently overinterpret these things
 - =>>> We just need an obsidian++ MCP (alias enhanced search; semantic search; wikilink enhanced search) !  ~~https://www.onyx.md/~~ for parsing power if we need it => memex
 - implementation plans shouldn't duplicate the git history, but augment it - or no, actually, implementation plans are only necessary for facilitating handovers and short-term context integrity
    - this means it's unnecessary to update plan with "finished"? just git commit / git log 
    - and updating the KNOWLDGE with the knowledge. plans should be ephemeral / deleted after commit, and after the learnings have been distilled at the end of the implementation.
    - only update the plan during execution, and only update knowledge at the end of execution? how manual / automatic can I make it?
    - architecture notes etc. are in project specific vaults
    - how to handle todos? single long doc prlly? or split into like "frontend todo" etc. prlly the former.
    - HANDOVER-feat-xyz.md files for the ephemeral ones?

