"""Tests for database operations."""

import sqlite3

import numpy as np
import pytest
import sqlite_vec

from memex_md_mcp.db import (
    delete_note,
    delete_vault,
    get_indexed_mtimes,
    get_note,
    get_note_rowid,
    get_outlinks,
    init_db,
    resolve_wikilink,
    search_fts,
    search_semantic,
    upsert_embedding,
    upsert_note,
)
from memex_md_mcp.parser import ParsedNote


@pytest.fixture
def conn():
    """In-memory database for testing."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def sample_note():
    return ParsedNote(
        title="test-note",
        aliases=["alias1", "alias2"],
        tags=["tag1", "tag2"],
        wikilinks=["other-note", "another"],
        content="This is test content about Python programming.",
    )


class TestInitDb:
    def test_creates_tables(self, conn):
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {row["name"] for row in tables}
        assert "notes" in table_names
        assert "wikilinks" in table_names

    def test_creates_fts(self, conn):
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {row["name"] for row in tables}
        assert "notes_fts" in table_names


class TestUpsertAndGetNote:
    def test_insert_and_retrieve(self, conn, sample_note):
        upsert_note(conn, "vault1", "path/to/note.md", sample_note, 1234567890.0, "abc123")

        result = get_note(conn, "vault1", "path/to/note.md")

        assert result is not None
        assert result.title == "test-note"
        assert result.aliases == ["alias1", "alias2"]
        assert result.tags == ["tag1", "tag2"]
        assert result.content == "This is test content about Python programming."
        assert result.mtime == 1234567890.0
        assert result.content_hash == "abc123"

    def test_update_existing(self, conn, sample_note):
        upsert_note(conn, "vault1", "note.md", sample_note, 1000.0, "hash1")

        updated_note = ParsedNote(
            title="updated-title",
            aliases=["new-alias"],
            tags=["new-tag"],
            wikilinks=[],
            content="Updated content.",
        )
        upsert_note(conn, "vault1", "note.md", updated_note, 2000.0, "hash2")

        result = get_note(conn, "vault1", "note.md")
        assert result is not None
        assert result.title == "updated-title"
        assert result.mtime == 2000.0
        assert result.content_hash == "hash2"

    def test_same_path_different_vaults(self, conn, sample_note):
        upsert_note(conn, "vault1", "note.md", sample_note, 1000.0, "hash1")

        other_note = ParsedNote(
            title="other",
            aliases=[],
            tags=[],
            wikilinks=[],
            content="Different vault content.",
        )
        upsert_note(conn, "vault2", "note.md", other_note, 2000.0, "hash2")

        result1 = get_note(conn, "vault1", "note.md")
        result2 = get_note(conn, "vault2", "note.md")
        assert result1 is not None
        assert result2 is not None
        assert result1.title == "test-note"
        assert result2.title == "other"


class TestDelete:
    def test_delete_note(self, conn, sample_note):
        upsert_note(conn, "vault1", "note.md", sample_note, 1000.0, "hash1")
        assert get_note(conn, "vault1", "note.md") is not None

        delete_note(conn, "vault1", "note.md")
        assert get_note(conn, "vault1", "note.md") is None

    def test_delete_vault(self, conn, sample_note):
        upsert_note(conn, "vault1", "note1.md", sample_note, 1000.0, "h1")
        upsert_note(conn, "vault1", "note2.md", sample_note, 1000.0, "h2")
        upsert_note(conn, "vault2", "note3.md", sample_note, 1000.0, "h3")

        deleted = delete_vault(conn, "vault1")

        assert deleted == 2
        assert get_note(conn, "vault1", "note1.md") is None
        assert get_note(conn, "vault1", "note2.md") is None
        assert get_note(conn, "vault2", "note3.md") is not None


class TestSearchFts:
    def test_basic_search(self, conn, sample_note):
        upsert_note(conn, "vault1", "note.md", sample_note, 1000.0, "hash1")

        results = search_fts(conn, "Python")

        assert len(results) == 1
        assert results[0].title == "test-note"

    def test_search_no_results(self, conn, sample_note):
        upsert_note(conn, "vault1", "note.md", sample_note, 1000.0, "hash1")

        results = search_fts(conn, "JavaScript")

        assert len(results) == 0

    def test_search_with_vault_filter(self, conn, sample_note):
        upsert_note(conn, "vault1", "note1.md", sample_note, 1000.0, "h1")
        upsert_note(conn, "vault2", "note2.md", sample_note, 1000.0, "h2")

        results = search_fts(conn, "Python", vault="vault1")

        assert len(results) == 1
        assert results[0].vault == "vault1"

    def test_search_in_title(self, conn):
        note = ParsedNote(
            title="python-patterns",
            aliases=[],
            tags=[],
            wikilinks=[],
            content="Some content.",
        )
        upsert_note(conn, "vault1", "python-patterns.md", note, 1000.0, "h1")

        results = search_fts(conn, "python")

        assert len(results) == 1

    def test_search_in_aliases(self, conn):
        note = ParsedNote(
            title="note",
            aliases=["python-guide"],
            tags=[],
            wikilinks=[],
            content="Some content.",
        )
        upsert_note(conn, "vault1", "note.md", note, 1000.0, "h1")

        results = search_fts(conn, "python")

        assert len(results) == 1


class TestSearchSemantic:
    @pytest.fixture
    def notes_with_embeddings(self, conn):
        """Create notes with embeddings for semantic search tests."""
        note1 = ParsedNote(
            title="python-note",
            aliases=[],
            tags=[],
            wikilinks=[],
            content="Python programming patterns.",
        )
        note2 = ParsedNote(
            title="rust-note",
            aliases=[],
            tags=[],
            wikilinks=[],
            content="Rust programming patterns.",
        )
        upsert_note(conn, "vault1", "python.md", note1, 1000.0, "h1")
        upsert_note(conn, "vault2", "rust.md", note2, 1000.0, "h2")

        # Create distinct embeddings (normalized)
        emb1 = np.array([1.0] + [0.0] * 767, dtype=np.float32)
        emb2 = np.array([0.0, 1.0] + [0.0] * 766, dtype=np.float32)

        rowid1 = get_note_rowid(conn, "vault1", "python.md")
        rowid2 = get_note_rowid(conn, "vault2", "rust.md")
        assert rowid1 is not None
        assert rowid2 is not None
        upsert_embedding(conn, rowid1, emb1)
        upsert_embedding(conn, rowid2, emb2)

        return {"emb1": emb1, "emb2": emb2}

    def test_basic_semantic_search(self, conn, notes_with_embeddings):
        query_emb = notes_with_embeddings["emb1"]
        results = search_semantic(conn, query_emb, limit=5)

        assert len(results) == 2
        assert results[0][0].title == "python-note"

    def test_semantic_search_with_vault_filter(self, conn, notes_with_embeddings):
        query_emb = notes_with_embeddings["emb1"]
        results = search_semantic(conn, query_emb, vault="vault2", limit=5)

        assert len(results) == 1
        assert results[0][0].vault == "vault2"


class TestGetIndexedMtimes:
    def test_returns_mtimes(self, conn, sample_note):
        upsert_note(conn, "vault1", "note1.md", sample_note, 1000.0, "h1")
        upsert_note(conn, "vault1", "note2.md", sample_note, 2000.0, "h2")
        upsert_note(conn, "vault2", "note3.md", sample_note, 3000.0, "h3")

        mtimes = get_indexed_mtimes(conn, "vault1")

        assert mtimes == {"note1.md": 1000.0, "note2.md": 2000.0}


class TestWikilinkResolution:
    def test_resolve_by_title(self, conn):
        note = ParsedNote(
            title="softmax",
            aliases=[],
            tags=[],
            wikilinks=[],
            content="Softmax function.",
        )
        upsert_note(conn, "vault1", "general/softmax.md", note, 1000.0, "h1")

        resolved = resolve_wikilink(conn, "vault1", "softmax")

        assert resolved == ["general/softmax.md"]

    def test_resolve_case_insensitive(self, conn):
        note = ParsedNote(
            title="Attention",
            aliases=[],
            tags=[],
            wikilinks=[],
            content="Attention mechanism.",
        )
        upsert_note(conn, "vault1", "attention.md", note, 1000.0, "h1")

        # lowercase query should match uppercase title
        resolved = resolve_wikilink(conn, "vault1", "attention")

        assert resolved == ["attention.md"]

    def test_resolve_multiple_matches(self, conn):
        # Two notes with same title but different case
        note1 = ParsedNote(title="Foo", aliases=[], tags=[], wikilinks=[], content="Uppercase.")
        note2 = ParsedNote(title="foo", aliases=[], tags=[], wikilinks=[], content="Lowercase.")
        upsert_note(conn, "vault1", "dir1/Foo.md", note1, 1000.0, "h1")
        upsert_note(conn, "vault1", "dir2/foo.md", note2, 1000.0, "h2")

        resolved = resolve_wikilink(conn, "vault1", "foo")

        assert len(resolved) == 2
        assert set(resolved) == {"dir1/Foo.md", "dir2/foo.md"}

    def test_resolve_unresolved_link(self, conn, sample_note):
        upsert_note(conn, "vault1", "note.md", sample_note, 1000.0, "h1")

        resolved = resolve_wikilink(conn, "vault1", "nonexistent")

        assert resolved == []

    def test_resolve_vault_scoped(self, conn):
        note = ParsedNote(title="shared", aliases=[], tags=[], wikilinks=[], content="Content.")
        upsert_note(conn, "vault1", "shared.md", note, 1000.0, "h1")
        upsert_note(conn, "vault2", "shared.md", note, 1000.0, "h2")

        # Should only find in specified vault
        resolved = resolve_wikilink(conn, "vault1", "shared")

        assert resolved == ["shared.md"]

    def test_get_outlinks_with_resolution(self, conn):
        # Create target notes
        target = ParsedNote(title="target", aliases=[], tags=[], wikilinks=[], content="Target.")
        upsert_note(conn, "vault1", "target.md", target, 1000.0, "h1")

        # Create source note with wikilinks
        source = ParsedNote(
            title="source",
            aliases=[],
            tags=[],
            wikilinks=["target", "missing"],
            content="Links to [[target]] and [[missing]].",
        )
        upsert_note(conn, "vault1", "source.md", source, 1000.0, "h2")

        outlinks = get_outlinks(conn, "vault1", "source.md")

        assert len(outlinks) == 2
        # First link should resolve
        assert ("target", ["target.md"]) in outlinks
        # Second link should be unresolved
        assert ("missing", []) in outlinks
