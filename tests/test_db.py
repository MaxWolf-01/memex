"""Tests for database operations."""

import sqlite3

import numpy as np
import pytest
import sqlite_vec

from memex_md.db import (
    delete_note,
    delete_root,
    get_indexed_mtimes,
    get_note,
    get_note_rowid,
    get_notes_needing_embeddings,
    get_outlinks,
    init_db,
    resolve_wikilink,
    search_semantic,
    upsert_embedding,
    upsert_note,
)
from memex_md.parser import ParsedNote

EMBEDDING_DIM = 768


@pytest.fixture
def conn():
    """In-memory database for testing."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    init_db(connection, model_name="test-model", embedding_dim=EMBEDDING_DIM)
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
        assert "metadata" in table_names

    def test_creates_vec_table(self, conn):
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {row["name"] for row in tables}
        assert "notes_vec" in table_names

    def test_model_change_drops_embeddings(self, conn, sample_note):
        upsert_note(conn, "/vault", "note.md", sample_note, 1000.0, "hash1")
        rowid = get_note_rowid(conn, "note.md")
        assert rowid is not None
        embedding = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        upsert_embedding(conn, rowid, embedding, "hash1")

        # Re-init with different model — should drop embeddings
        init_db(conn, model_name="different-model", embedding_dim=EMBEDDING_DIM)

        note = get_note(conn, "note.md")
        assert note is not None
        # embedding_hash should be cleared
        row = conn.execute("SELECT embedding_hash FROM notes WHERE path = ?", ("note.md",)).fetchone()
        assert row["embedding_hash"] is None


class TestUpsertAndGetNote:
    def test_insert_and_retrieve(self, conn, sample_note):
        upsert_note(conn, "/vault1", "path/to/note.md", sample_note, 1234567890.0, "abc123")

        result = get_note(conn, "path/to/note.md")

        assert result is not None
        assert result.title == "test-note"
        assert result.aliases == ["alias1", "alias2"]
        assert result.tags == ["tag1", "tag2"]
        assert result.content == "This is test content about Python programming."
        assert result.mtime == 1234567890.0
        assert result.content_hash == "abc123"

    def test_insert_and_retrieve_with_root(self, conn, sample_note):
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "hash1")

        result = get_note(conn, "note.md", root="/vault1")
        assert result is not None
        assert result.root == "/vault1"

    def test_update_existing(self, conn, sample_note):
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "hash1")

        updated_note = ParsedNote(
            title="updated-title",
            aliases=["new-alias"],
            tags=["new-tag"],
            wikilinks=[],
            content="Updated content.",
        )
        upsert_note(conn, "/vault1", "note.md", updated_note, 2000.0, "hash2")

        result = get_note(conn, "note.md")
        assert result is not None
        assert result.title == "updated-title"
        assert result.mtime == 2000.0
        assert result.content_hash == "hash2"

    def test_same_path_different_roots(self, conn, sample_note):
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "hash1")

        other_note = ParsedNote(
            title="other",
            aliases=[],
            tags=[],
            wikilinks=[],
            content="Different root content.",
        )
        upsert_note(conn, "/vault2", "note.md", other_note, 2000.0, "hash2")

        result1 = get_note(conn, "note.md", root="/vault1")
        result2 = get_note(conn, "note.md", root="/vault2")
        assert result1 is not None
        assert result2 is not None
        assert result1.title == "test-note"
        assert result2.title == "other"


class TestDelete:
    def test_delete_note(self, conn, sample_note):
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "hash1")
        assert get_note(conn, "note.md") is not None

        delete_note(conn, "/vault1", "note.md")
        assert get_note(conn, "note.md") is None

    def test_delete_root(self, conn, sample_note):
        upsert_note(conn, "/vault1", "note1.md", sample_note, 1000.0, "h1")
        upsert_note(conn, "/vault1", "note2.md", sample_note, 1000.0, "h2")
        upsert_note(conn, "/vault2", "note3.md", sample_note, 1000.0, "h3")

        deleted = delete_root(conn, "/vault1")

        assert deleted == 2
        assert get_note(conn, "note1.md", root="/vault1") is None
        assert get_note(conn, "note2.md", root="/vault1") is None
        assert get_note(conn, "note3.md", root="/vault2") is not None


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
        upsert_note(conn, "/vault1", "python.md", note1, 1000.0, "h1")
        upsert_note(conn, "/vault2", "rust.md", note2, 1000.0, "h2")

        emb1 = np.array([1.0] + [0.0] * (EMBEDDING_DIM - 1), dtype=np.float32)
        emb2 = np.array([0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2), dtype=np.float32)

        rowid1 = get_note_rowid(conn, "python.md")
        rowid2 = get_note_rowid(conn, "rust.md")
        assert rowid1 is not None
        assert rowid2 is not None
        upsert_embedding(conn, rowid1, emb1, "h1")
        upsert_embedding(conn, rowid2, emb2, "h2")

        return {"emb1": emb1, "emb2": emb2}

    def test_basic_semantic_search(self, conn, notes_with_embeddings):
        query_emb = notes_with_embeddings["emb1"]
        results = search_semantic(conn, query_emb, limit=5)

        assert len(results) == 2
        assert results[0][0].title == "python-note"

    def test_semantic_search_limit(self, conn, notes_with_embeddings):
        query_emb = notes_with_embeddings["emb1"]
        results = search_semantic(conn, query_emb, limit=1)

        assert len(results) == 1


class TestGetIndexedMtimes:
    def test_returns_mtimes(self, conn, sample_note):
        upsert_note(conn, "/vault1", "note1.md", sample_note, 1000.0, "h1")
        upsert_note(conn, "/vault1", "note2.md", sample_note, 2000.0, "h2")
        upsert_note(conn, "/vault2", "note3.md", sample_note, 3000.0, "h3")

        mtimes = get_indexed_mtimes(conn, "/vault1")

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
        upsert_note(conn, "/vault1", "general/softmax.md", note, 1000.0, "h1")

        resolved = resolve_wikilink(conn, "softmax")

        assert resolved == ["general/softmax.md"]

    def test_resolve_case_insensitive(self, conn):
        note = ParsedNote(
            title="Attention",
            aliases=[],
            tags=[],
            wikilinks=[],
            content="Attention mechanism.",
        )
        upsert_note(conn, "/vault1", "attention.md", note, 1000.0, "h1")

        resolved = resolve_wikilink(conn, "attention")

        assert resolved == ["attention.md"]

    def test_resolve_multiple_matches(self, conn):
        note1 = ParsedNote(title="Foo", aliases=[], tags=[], wikilinks=[], content="Uppercase.")
        note2 = ParsedNote(title="foo", aliases=[], tags=[], wikilinks=[], content="Lowercase.")
        upsert_note(conn, "/vault1", "dir1/Foo.md", note1, 1000.0, "h1")
        upsert_note(conn, "/vault1", "dir2/foo.md", note2, 1000.0, "h2")

        resolved = resolve_wikilink(conn, "foo")

        assert len(resolved) == 2
        assert set(resolved) == {"dir1/Foo.md", "dir2/foo.md"}

    def test_resolve_unresolved_link(self, conn, sample_note):
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "h1")

        resolved = resolve_wikilink(conn, "nonexistent")

        assert resolved == []

    def test_resolve_ranks_exact_case_first(self, conn):
        note_upper = ParsedNote(title="Security", aliases=[], tags=[], wikilinks=[], content="Upper.")
        note_lower = ParsedNote(title="security", aliases=[], tags=[], wikilinks=[], content="Lower.")
        upsert_note(conn, "/vault1", "deep/Security.md", note_upper, 1000.0, "h1")
        upsert_note(conn, "/vault1", "security.md", note_lower, 1000.0, "h2")

        resolved = resolve_wikilink(conn, "security")
        assert resolved[0] == "security.md"

    def test_resolve_ranks_shallow_path_first(self, conn):
        note1 = ParsedNote(title="readme", aliases=[], tags=[], wikilinks=[], content="Deep.")
        note2 = ParsedNote(title="readme", aliases=[], tags=[], wikilinks=[], content="Shallow.")
        upsert_note(conn, "/vault1", "deep/nested/readme.md", note1, 1000.0, "h1")
        upsert_note(conn, "/vault1", "readme.md", note2, 1000.0, "h2")

        resolved = resolve_wikilink(conn, "readme")
        assert resolved[0] == "readme.md"

    def test_resolve_exact_case_beats_shallow_path(self, conn):
        note_exact = ParsedNote(title="Security", aliases=[], tags=[], wikilinks=[], content="Exact.")
        note_wrong = ParsedNote(title="SECURITY", aliases=[], tags=[], wikilinks=[], content="Wrong case.")
        upsert_note(conn, "/vault1", "deep/Security.md", note_exact, 1000.0, "h1")
        upsert_note(conn, "/vault1", "SECURITY.md", note_wrong, 1000.0, "h2")

        resolved = resolve_wikilink(conn, "Security")
        assert resolved[0] == "deep/Security.md"

    def test_get_outlinks_with_resolution(self, conn):
        target = ParsedNote(title="target", aliases=[], tags=[], wikilinks=[], content="Target.")
        upsert_note(conn, "/vault1", "target.md", target, 1000.0, "h1")

        source = ParsedNote(
            title="source",
            aliases=[],
            tags=[],
            wikilinks=["target", "missing"],
            content="Links to [[target]] and [[missing]].",
        )
        upsert_note(conn, "/vault1", "source.md", source, 1000.0, "h2")

        outlinks = get_outlinks(conn, "/vault1", "source.md")

        assert len(outlinks) == 2
        assert ("target", ["target.md"]) in outlinks
        assert ("missing", []) in outlinks


class TestEmbeddingHashTracking:
    """Tests for embedding staleness detection via embedding_hash."""

    def test_new_note_needs_embedding(self, conn, sample_note):
        """Note without embedding_hash should need embedding."""
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "hash1")

        needs = get_notes_needing_embeddings(conn, "/vault1")

        key = "/vault1/note.md"
        assert key in needs
        rowid, root, title, content, content_hash = needs[key]
        assert title == sample_note.title
        assert content_hash == "hash1"

    def test_embedded_note_not_returned(self, conn, sample_note):
        """Note with matching embedding_hash should not need embedding."""
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "hash1")
        rowid = get_note_rowid(conn, "note.md")
        assert rowid is not None
        embedding = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        upsert_embedding(conn, rowid, embedding, "hash1")

        needs = get_notes_needing_embeddings(conn, "/vault1")

        assert "/vault1/note.md" not in needs

    def test_stale_embedding_returned(self, conn, sample_note):
        """Note with mismatched embedding_hash should need re-embedding."""
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "hash1")
        rowid = get_note_rowid(conn, "note.md")
        assert rowid is not None
        embedding = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        upsert_embedding(conn, rowid, embedding, "hash1")
        upsert_note(conn, "/vault1", "note.md", sample_note, 2000.0, "hash2")

        needs = get_notes_needing_embeddings(conn, "/vault1")

        key = "/vault1/note.md"
        assert key in needs
        assert needs[key][4] == "hash2"  # content_hash

    def test_root_scoped(self, conn, sample_note):
        """Should only return notes from specified root."""
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "h1")
        upsert_note(conn, "/vault2", "note.md", sample_note, 1000.0, "h2")

        needs = get_notes_needing_embeddings(conn, "/vault1")

        assert len(needs) == 1
        assert "/vault1/note.md" in needs

    def test_all_roots_when_none(self, conn, sample_note):
        """Should return notes from all roots when root is None."""
        upsert_note(conn, "/vault1", "note.md", sample_note, 1000.0, "h1")
        upsert_note(conn, "/vault2", "note.md", sample_note, 1000.0, "h2")

        needs = get_notes_needing_embeddings(conn)

        assert len(needs) == 2
