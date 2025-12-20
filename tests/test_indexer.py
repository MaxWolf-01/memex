"""Tests for indexer."""

import sqlite3
import time

import pytest
import sqlite_vec

from memex_md_mcp.db import get_note, init_db, search_fts
from memex_md_mcp.indexer import content_hash, discover_files, index_all_vaults, index_vault


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    init_db(connection)
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

    # Non-markdown file should be ignored
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


class TestIndexVault:
    def test_indexes_all_files(self, conn, temp_vault):
        stats = index_vault(conn, "test", temp_vault)

        assert stats.added == 3
        assert stats.updated == 0
        assert stats.deleted == 0

    def test_detects_changes(self, conn, temp_vault):
        index_vault(conn, "test", temp_vault)

        # Modify a file (ensure mtime changes)
        time.sleep(0.01)
        (temp_vault / "note1.md").write_text("# Note 1\n\nUpdated content.")

        stats = index_vault(conn, "test", temp_vault)

        assert stats.added == 0
        assert stats.updated == 1
        assert stats.unchanged == 2

    def test_detects_deletions(self, conn, temp_vault):
        index_vault(conn, "test", temp_vault)

        (temp_vault / "note1.md").unlink()

        stats = index_vault(conn, "test", temp_vault)

        assert stats.deleted == 1
        assert get_note(conn, "test", "note1.md") is None

    def test_detects_new_files(self, conn, temp_vault):
        index_vault(conn, "test", temp_vault)

        (temp_vault / "new.md").write_text("# New\n\nNew content.")

        stats = index_vault(conn, "test", temp_vault)

        assert stats.added == 1

    def test_content_searchable_after_index(self, conn, temp_vault):
        index_vault(conn, "test", temp_vault)

        results = search_fts(conn, "python")

        assert len(results) == 1
        assert results[0].path == "note1.md"

    def test_wikilinks_indexed(self, conn, temp_vault):
        index_vault(conn, "test", temp_vault)

        links = conn.execute(
            "SELECT target_raw FROM wikilinks WHERE source_path = ?", ("subfolder/nested.md",)
        ).fetchall()

        assert len(links) == 1
        assert links[0]["target_raw"] == "note1"


class TestIndexAllVaults:
    def test_indexes_multiple_vaults(self, conn, tmp_path):
        vault1 = tmp_path / "vault1"
        vault1.mkdir()
        (vault1 / "note.md").write_text("Vault 1 content")

        vault2 = tmp_path / "vault2"
        vault2.mkdir()
        (vault2 / "note.md").write_text("Vault 2 content")

        results = index_all_vaults(conn, {"v1": vault1, "v2": vault2})

        assert "v1" in results
        assert "v2" in results
        assert results["v1"].added == 1
        assert results["v2"].added == 1

    def test_handles_missing_vault(self, conn, tmp_path):
        missing = tmp_path / "does-not-exist"

        results = index_all_vaults(conn, {"missing": missing})

        assert len(results["missing"].errors) == 1
