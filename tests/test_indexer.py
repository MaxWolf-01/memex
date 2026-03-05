"""Tests for indexer."""

import sqlite3
import time

import pytest
import sqlite_vec

from memex_md.db import get_note, init_db
from memex_md.indexer import content_hash, discover_files, index_root, index_vault

EMBEDDING_DIM = 768


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    init_db(connection, model_name="test-model", embedding_dim=EMBEDDING_DIM)
    yield connection
    connection.close()


@pytest.fixture
def temp_vault(tmp_path):
    """Create a temporary vault with some markdown files."""
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "note1.md").write_text("# Note 1\n\nContent about #python.")
    (vault / "note2.md").write_text("# Note 2\n\nContent about #rust.")

    subfolder = vault / "subfolder"
    subfolder.mkdir()
    (subfolder / "nested.md").write_text("# Nested\n\nNested content [[note1]].")

    (vault / "readme.txt").write_text("Ignore me")

    return vault


class TestContentHash:
    def test_consistent_hash(self):
        assert content_hash("hello") == content_hash("hello")

    def test_different_content_different_hash(self):
        assert content_hash("hello") != content_hash("world")


class TestDiscoverFiles:
    def test_finds_md_files(self, temp_vault):
        files = discover_files(temp_vault)

        assert len(files) == 3
        assert "note1.md" in files
        assert "note2.md" in files
        assert "subfolder/nested.md" in files

    def test_ignores_non_md_files(self, temp_vault):
        files = discover_files(temp_vault)

        assert "readme.txt" not in files

    def test_returns_mtimes(self, temp_vault):
        files = discover_files(temp_vault)

        for _, mtime in files.items():
            assert isinstance(mtime, float)
            assert mtime > 0


class TestIndexRoot:
    def test_indexes_all_files(self, conn, temp_vault):
        stats = index_root(conn, temp_vault)

        assert stats.added == 3
        assert stats.updated == 0
        assert stats.deleted == 0

    def test_detects_changes(self, conn, temp_vault):
        index_root(conn, temp_vault)

        time.sleep(0.01)
        (temp_vault / "note1.md").write_text("# Note 1\n\nUpdated content.")

        stats = index_root(conn, temp_vault)

        assert stats.added == 0
        assert stats.updated == 1
        assert stats.unchanged == 2

    def test_detects_deletions(self, conn, temp_vault):
        index_root(conn, temp_vault)

        (temp_vault / "note1.md").unlink()

        stats = index_root(conn, temp_vault)

        assert stats.deleted == 1
        assert get_note(conn, "note1.md", root=str(temp_vault)) is None

    def test_detects_new_files(self, conn, temp_vault):
        index_root(conn, temp_vault)

        (temp_vault / "new.md").write_text("# New\n\nNew content.")

        stats = index_root(conn, temp_vault)

        assert stats.added == 1

    def test_wikilinks_indexed(self, conn, temp_vault):
        index_root(conn, temp_vault)

        links = conn.execute(
            "SELECT target_raw FROM wikilinks WHERE source_path = ?", ("subfolder/nested.md",)
        ).fetchall()

        assert len(links) == 1
        assert links[0]["target_raw"] == "note1"


class TestDiscoverFilesIgnore:
    def test_ignores_directory_by_name(self, temp_vault):
        nm = temp_vault / "node_modules"
        nm.mkdir()
        (nm / "package.md").write_text("# Package\nShould be ignored.")

        files = discover_files(temp_vault, ignore=["node_modules"])

        assert "node_modules/package.md" not in files
        assert len(files) == 3

    def test_ignores_file_by_pattern(self, temp_vault):
        (temp_vault / "auto.generated.md").write_text("# Auto\nGenerated file.")

        files = discover_files(temp_vault, ignore=["*.generated.md"])

        assert "auto.generated.md" not in files
        assert len(files) == 3

    def test_ignores_nested_directory(self, temp_vault):
        deep = temp_vault / "a" / "b" / "node_modules"
        deep.mkdir(parents=True)
        (deep / "deep.md").write_text("# Deep\nDeeply nested.")

        files = discover_files(temp_vault, ignore=["node_modules"])

        assert all("node_modules" not in path for path in files)

    def test_no_ignore_patterns_unchanged(self, temp_vault):
        files_without = discover_files(temp_vault)
        files_with_empty = discover_files(temp_vault, ignore=[])

        assert files_without == files_with_empty


class TestIndexVault:
    def test_indexes_multiple_roots(self, conn, tmp_path):
        root1 = tmp_path / "root1"
        root1.mkdir()
        (root1 / "note.md").write_text("Root 1 content")

        root2 = tmp_path / "root2"
        root2.mkdir()
        (root2 / "note.md").write_text("Root 2 content")

        stats = index_vault(conn, [root1, root2])

        assert stats.added == 2

    def test_handles_missing_root(self, conn, tmp_path):
        missing = tmp_path / "does-not-exist"

        stats = index_vault(conn, [missing])

        assert len(stats.errors) == 1
