"""Tests for server search logic."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import memex_md_mcp.db as db_module
from memex_md_mcp.server import search


@pytest.fixture
def temp_vault():
    """Create a temporary vault with test notes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)

        # Create test notes
        (vault_path / "python.md").write_text(
            "---\naliases: [py]\ntags: [programming]\n---\nPython is a programming language."
        )
        (vault_path / "rust.md").write_text(
            "---\naliases: [rs]\ntags: [programming]\n---\nRust is a systems programming language."
        )
        (vault_path / "javascript.md").write_text(
            "---\ntags: [programming, web]\n---\nJavaScript runs in browsers."
        )
        (vault_path / "auth.md").write_text(
            "---\ntags: [security]\n---\nWe decided to use OAuth for authentication. JWT tokens for sessions."
        )
        (vault_path / "database.md").write_text(
            "---\ntags: [backend]\n---\nUsing PostgreSQL for the main database."
        )

        yield vault_path


@pytest.fixture
def vault_env(temp_vault):
    """Set MEMEX_VAULTS env var and use isolated temp DB."""
    with tempfile.TemporaryDirectory() as db_dir:
        temp_db_path = Path(db_dir) / "test.db"
        with (
            patch.dict(os.environ, {"MEMEX_VAULTS": str(temp_vault)}),
            patch.object(db_module, "DB_PATH", temp_db_path),
        ):
            yield temp_vault


class TestSearchQueryOptional:
    def test_fts_only_with_keywords(self, vault_env):
        """When query=None and keywords provided, runs FTS-only."""
        result = search(query=None, keywords=["OAuth"], limit=5)

        # Should find auth.md via FTS
        assert str(vault_env) in result
        paths = result[str(vault_env)]
        assert any("auth" in p["path"] for p in paths)

    def test_error_when_both_none(self, vault_env):
        """Returns error when both query and keywords are None."""
        result = search(query=None, keywords=None)

        assert "error" in result
        assert "Provide query" in result["error"]

    def test_semantic_only(self, vault_env):
        """When only query provided, runs semantic search."""
        result = search(query="What programming language is good for systems?", limit=5)

        # Should return results (semantic search)
        assert str(vault_env) in result or "message" in result

    def test_combined_query_and_keywords(self, vault_env):
        """When both provided, combines semantic and FTS via RRF."""
        result = search(
            query="What authentication method did we choose?",
            keywords=["OAuth", "JWT"],
            limit=5,
        )

        assert str(vault_env) in result
        paths = result[str(vault_env)]
        assert any("auth" in p["path"] for p in paths)


class TestSearchPagination:
    def test_page_1_returns_first_results(self, vault_env):
        """Page 1 returns first `limit` results."""
        result = search(query="programming language", limit=2, page=1)

        if str(vault_env) in result:
            total_results = sum(len(v) for v in result.values() if isinstance(v, list))
            assert total_results <= 2

    def test_page_2_returns_different_results(self, vault_env):
        """Page 2 returns different results than page 1."""
        result1 = search(query="programming language", limit=2, page=1)
        result2 = search(query="programming language", limit=2, page=2)

        # Get paths from both pages
        paths1 = set()
        paths2 = set()
        for vault_results in result1.values():
            if isinstance(vault_results, list):
                for r in vault_results:
                    if isinstance(r, dict) and "path" in r:
                        paths1.add(r["path"])
        for vault_results in result2.values():
            if isinstance(vault_results, list):
                for r in vault_results:
                    if isinstance(r, dict) and "path" in r:
                        paths2.add(r["path"])

        # If we have results on both pages, they should be different
        if paths1 and paths2:
            assert paths1 != paths2

    def test_page_beyond_results_empty(self, vault_env):
        """Page far beyond results returns no results message."""
        result = search(query="programming", limit=2, page=100)

        # Should have message about no results
        assert "message" in result or all(
            len(v) == 0 for v in result.values() if isinstance(v, list)
        )

    def test_concise_mode_pagination(self, vault_env):
        """Pagination works with concise=True."""
        result = search(query="programming", limit=2, page=1, concise=True)

        if str(vault_env) in result:
            # Concise mode returns list of paths, not dicts
            paths = result[str(vault_env)]
            assert all(isinstance(p, str) for p in paths)
            assert len(paths) <= 2
