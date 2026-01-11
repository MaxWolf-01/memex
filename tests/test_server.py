"""Tests for server search logic."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import memex_md_mcp.db as db_module
from memex_md_mcp.server import explore, rename, resolve_vault_path, search


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
        result = search(query=None, keywords=["OAuth"], limit=5, concise=False)

        # Should find auth.md via FTS
        assert str(vault_env) in result
        paths = result[str(vault_env)]
        assert any("auth" in p["path"] for p in paths)

    @pytest.mark.usefixtures("vault_env")
    def test_error_when_both_none(self):
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
            concise=False,
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

    @pytest.mark.usefixtures("vault_env")
    def test_page_2_returns_different_results(self):
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

    @pytest.mark.usefixtures("vault_env")
    def test_page_beyond_results_empty(self):
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


class TestVaultPathResolution:
    def test_resolve_vault_path_none_returns_none(self):
        """None vault returns None."""
        vaults = {"/abs/path": Path("/abs/path")}
        assert resolve_vault_path(None, vaults) is None

    def test_resolve_vault_path_relative_to_absolute(self, temp_vault):
        """Relative path resolves to absolute when it matches a configured vault."""
        vaults = {str(temp_vault): temp_vault}
        # Create a relative path that would resolve to temp_vault
        # We need to be in a specific dir for this, so use the absolute path directly
        result = resolve_vault_path(str(temp_vault), vaults)
        assert result == str(temp_vault)

    def test_resolve_vault_path_unknown_returns_original(self):
        """Unknown vault path returns original (for error handling later)."""
        vaults = {"/configured/vault": Path("/configured/vault")}
        result = resolve_vault_path("/unknown/path", vaults)
        assert result == "/unknown/path"

    @pytest.mark.usefixtures("vault_env")
    def test_search_invalid_vault_returns_error(self):
        """Search with invalid vault returns error instead of silently failing."""
        result = search(query="test", vault="/nonexistent/vault")
        assert "error" in result
        assert "not found" in result["error"]

    def test_search_valid_vault_works(self, vault_env):
        """Search with valid absolute vault path works."""
        result = search(query="programming", vault=str(vault_env))
        # Should not have an error
        assert "error" not in result


class TestSemanticDisabled:
    """Tests for MEMEX_DISABLE_SEMANTIC=1 behavior."""

    @pytest.fixture
    def vault_env_no_semantic(self, temp_vault):
        """Set up vault with semantic search disabled."""
        with tempfile.TemporaryDirectory() as db_dir:
            temp_db_path = Path(db_dir) / "test.db"
            with (
                patch.dict(os.environ, {"MEMEX_VAULTS": str(temp_vault), "MEMEX_DISABLE_SEMANTIC": "1"}),
                patch.object(db_module, "DB_PATH", temp_db_path),
            ):
                yield temp_vault

    def test_fts_works_when_semantic_disabled(self, vault_env_no_semantic):
        """FTS still works when semantic is disabled."""
        result = search(keywords=["OAuth"], limit=5, concise=False)

        assert str(vault_env_no_semantic) in result
        paths = result[str(vault_env_no_semantic)]
        assert any("auth" in p["path"] for p in paths)

    def test_query_ignored_when_semantic_disabled(self, vault_env_no_semantic):
        """Query parameter is ignored with info message when semantic disabled."""
        result = search(query="authentication", keywords=["OAuth"], limit=5, vault=str(vault_env_no_semantic))

        assert "info" in result
        assert "disabled" in result["info"].lower()

    def test_explore_similar_empty_when_semantic_disabled(self, vault_env_no_semantic):
        """Explore returns empty similar list when semantic disabled."""
        result = explore(note_path="auth.md", vault=str(vault_env_no_semantic))

        assert "error" not in result
        assert result["similar"] == []


class TestMdExtensionOptional:
    """Tests for .md extension being optional in note paths."""

    def test_explore_with_extension(self, vault_env):
        """Explore works with .md extension."""
        result = explore(note_path="auth.md", vault=str(vault_env))

        assert "error" not in result
        assert result["note"]["path"] == "auth.md"

    def test_explore_without_extension(self, vault_env):
        """Explore works without .md extension."""
        result = explore(note_path="auth", vault=str(vault_env))

        assert "error" not in result
        assert result["note"]["path"] == "auth.md"

    def test_explore_nonexistent_without_extension(self, vault_env):
        """Explore returns error for nonexistent note without extension."""
        result = explore(note_path="nonexistent", vault=str(vault_env))

        assert "error" in result

    def test_explore_by_title_unique(self, vault_env):
        """Explore works with just title if unique in vault."""
        result = explore(note_path="auth", vault=str(vault_env))

        assert "error" not in result
        assert result["note"]["path"] == "auth.md"

    def test_explore_by_title_in_subdir(self, vault_env):
        """Explore by title finds file in subdirectory."""
        # Create a file in a subdirectory
        subdir = vault_env / "subdir"
        subdir.mkdir()
        (subdir / "unique-note.md").write_text("# Unique Note\nContent here.")

        result = explore(note_path="unique-note", vault=str(vault_env))

        assert "error" not in result
        assert result["note"]["path"] == "subdir/unique-note.md"

    def test_explore_by_title_ambiguous_error(self, vault_env):
        """Explore by title errors when multiple files have same title."""
        # Create another file with same title as existing
        subdir = vault_env / "subdir"
        subdir.mkdir(exist_ok=True)
        (subdir / "auth.md").write_text("# Auth\nAnother auth file.")

        result = explore(note_path="auth", vault=str(vault_env))

        assert "error" in result
        assert "Multiple notes" in result["error"]
        assert "auth.md" in result["error"]
        assert "subdir/auth.md" in result["error"]


class TestRename:
    """Tests for rename tool."""

    @pytest.fixture
    def vault_with_links(self):
        """Create a vault with notes that link to each other."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)

            # Create notes with wikilinks
            (vault_path / "backend.md").write_text(
                "# Backend\nSee also [[auth]] and [[database]]."
            )
            (vault_path / "auth.md").write_text(
                "# Auth\nAuthentication module. Used by [[backend]]."
            )
            (vault_path / "database.md").write_text(
                "# Database\nPostgreSQL setup."
            )
            (vault_path / "overview.md").write_text(
                "# Overview\nMain components: [[backend]], [[auth]], [[database]]."
            )

            yield vault_path

    @pytest.fixture
    def vault_env_links(self, vault_with_links):
        """Set up vault with links for rename testing."""
        with tempfile.TemporaryDirectory() as db_dir:
            temp_db_path = Path(db_dir) / "test.db"
            with (
                patch.dict(os.environ, {"MEMEX_VAULTS": str(vault_with_links)}),
                patch.object(db_module, "DB_PATH", temp_db_path),
            ):
                yield vault_with_links

    def test_rename_simple(self, vault_env_links):
        """Rename a note without any backlinks."""
        result = rename(note_path="database", new_name="postgres", vault=str(vault_env_links))

        assert "error" not in result
        assert result["old_path"] == "database.md"
        assert result["new_path"] == "postgres.md"
        assert (vault_env_links / "postgres.md").exists()
        assert not (vault_env_links / "database.md").exists()

    def test_rename_updates_backlinks(self, vault_env_links):
        """Rename updates wikilinks in files that reference the renamed note."""
        result = rename(note_path="auth.md", new_name="authentication", vault=str(vault_env_links))

        assert "error" not in result
        assert result["updated_count"] >= 1

        # Check that backend.md now links to [[authentication]]
        backend_content = (vault_env_links / "backend.md").read_text()
        assert "[[authentication]]" in backend_content
        assert "[[auth]]" not in backend_content

        # Check that overview.md also updated
        overview_content = (vault_env_links / "overview.md").read_text()
        assert "[[authentication]]" in overview_content

    def test_rename_target_exists_error(self, vault_env_links):
        """Rename returns error if target already exists."""
        result = rename(note_path="auth", new_name="backend", vault=str(vault_env_links))

        assert "error" in result
        assert "exists" in result["error"].lower()

    def test_rename_source_not_found(self, vault_env_links):
        """Rename returns error if source note doesn't exist."""
        result = rename(note_path="nonexistent", new_name="something", vault=str(vault_env_links))

        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_rename_by_title_resolves_subdirectory(self, vault_env_links):
        """Rename can find a note by unique title even if it's in a subdirectory."""
        vault_path = vault_env_links
        # Create a note in a subdirectory with a unique title
        (vault_path / "docs").mkdir()
        (vault_path / "docs" / "guide.md").write_text("# Guide\nSome documentation.")

        # Should resolve "guide" to "docs/guide.md"
        result = rename(note_path="guide", new_name="manual", vault=str(vault_path))

        assert "error" not in result
        assert result["old_path"] == "docs/guide.md"
        assert result["new_path"] == "docs/manual.md"
        assert (vault_path / "docs" / "manual.md").exists()
        assert not (vault_path / "docs" / "guide.md").exists()

    @pytest.fixture
    def vault_with_complex_links(self):
        """Create a vault with various wikilink formats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)

            # Note with various link formats
            (vault_path / "index.md").write_text(
                "# Index\n"
                "Simple link: [[target]]\n"
                "With alias: [[target|Display Name]]\n"
                "With heading: [[target#section]]\n"
                "With both: [[target#section|Section Link]]\n"
                "Case variant: [[Target]]\n"
                "Block ref: [[target#^abc123]]\n"
                "Block ref with alias: [[target#^abc123|Block Link]]\n"
            )
            (vault_path / "target.md").write_text("# Target\nThis is the target note.")

            # Subdirectory structure
            (vault_path / "subdir").mkdir()
            (vault_path / "subdir" / "nested.md").write_text(
                "# Nested\nLinks to [[target]] from subdir."
            )

            yield vault_path

    @pytest.fixture
    def vault_env_complex(self, vault_with_complex_links):
        """Set up vault with complex links for testing."""
        with tempfile.TemporaryDirectory() as db_dir:
            temp_db_path = Path(db_dir) / "test.db"
            with (
                patch.dict(os.environ, {"MEMEX_VAULTS": str(vault_with_complex_links)}),
                patch.object(db_module, "DB_PATH", temp_db_path),
            ):
                yield vault_with_complex_links

    def test_rename_preserves_alias(self, vault_env_complex):
        """Rename preserves display aliases in wikilinks."""
        result = rename(note_path="target", new_name="destination", vault=str(vault_env_complex))

        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[destination|Display Name]]" in content

    def test_rename_preserves_heading(self, vault_env_complex):
        """Rename preserves heading references in wikilinks."""
        result = rename(note_path="target", new_name="destination", vault=str(vault_env_complex))

        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[destination#section]]" in content

    def test_rename_preserves_heading_and_alias(self, vault_env_complex):
        """Rename preserves both heading and alias in wikilinks."""
        result = rename(note_path="target", new_name="destination", vault=str(vault_env_complex))

        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[destination#section|Section Link]]" in content

    def test_rename_case_insensitive_matching(self, vault_env_complex):
        """Rename updates all case variants of wikilinks."""
        result = rename(note_path="target", new_name="destination", vault=str(vault_env_complex))

        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        # Both [[target]] and [[Target]] should be updated to [[destination]]
        assert "[[target]]" not in content
        assert "[[Target]]" not in content
        # Should have multiple [[destination]] links now
        assert content.count("[[destination") >= 2

    def test_rename_updates_links_in_subdirs(self, vault_env_complex):
        """Rename updates wikilinks in files in subdirectories."""
        result = rename(note_path="target", new_name="destination", vault=str(vault_env_complex))

        assert "error" not in result
        nested_content = (vault_env_complex / "subdir" / "nested.md").read_text()
        assert "[[destination]]" in nested_content
        assert "[[target]]" not in nested_content

    def test_rename_preserves_block_reference(self, vault_env_complex):
        """Rename preserves block references like [[note#^blockid]]."""
        result = rename(note_path="target", new_name="destination", vault=str(vault_env_complex))

        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[destination#^abc123]]" in content
        assert "[[destination#^abc123|Block Link]]" in content


class TestRenameEdgeCases:
    """Tests for rename edge cases: path-based links, same-name files, ambiguity."""

    @pytest.fixture
    def vault_with_path_links(self):
        """Vault with path-based wikilinks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)

            # Create subdirectory structure
            (vault_path / "docs").mkdir()
            (vault_path / "docs" / "guide.md").write_text("# Guide\nThis is a guide.")

            # Index file with both title and path-based links
            (vault_path / "index.md").write_text(
                "# Index\n"
                "Title link: [[guide]]\n"
                "Path link: [[docs/guide]]\n"
                "Path with alias: [[docs/guide|The Guide]]\n"
            )

            yield vault_path

    @pytest.fixture
    def vault_env_path_links(self, vault_with_path_links):
        """Set up vault with path-based links."""
        with tempfile.TemporaryDirectory() as db_dir:
            temp_db_path = Path(db_dir) / "test.db"
            with (
                patch.dict(os.environ, {"MEMEX_VAULTS": str(vault_with_path_links)}),
                patch.object(db_module, "DB_PATH", temp_db_path),
            ):
                yield vault_with_path_links

    def test_rename_updates_path_based_links(self, vault_env_path_links):
        """Rename updates [[subdir/note]] style path-based links."""
        result = rename(note_path="docs/guide", new_name="manual", vault=str(vault_env_path_links))

        assert "error" not in result
        content = (vault_env_path_links / "index.md").read_text()

        # Path-based links should be updated with full path
        assert "[[docs/manual]]" in content
        assert "[[docs/manual|The Guide]]" in content
        # Title-based link should also be updated
        assert "[[manual]]" in content
        # Old links should be gone
        assert "[[guide]]" not in content
        assert "[[docs/guide]]" not in content

    @pytest.fixture
    def vault_with_same_name_different_dirs(self):
        """Vault with same filename in different directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)

            # Create two files with same name in different dirs
            (vault_path / "frontend").mkdir()
            (vault_path / "backend").mkdir()
            (vault_path / "frontend" / "config.md").write_text("# Frontend Config")
            (vault_path / "backend" / "config.md").write_text("# Backend Config")

            # Index with various links
            (vault_path / "index.md").write_text(
                "# Index\n"
                "Ambiguous: [[config]]\n"
                "Frontend specific: [[frontend/config]]\n"
                "Backend specific: [[backend/config]]\n"
            )

            yield vault_path

    @pytest.fixture
    def vault_env_same_name(self, vault_with_same_name_different_dirs):
        """Set up vault with same-name files."""
        with tempfile.TemporaryDirectory() as db_dir:
            temp_db_path = Path(db_dir) / "test.db"
            with (
                patch.dict(os.environ, {"MEMEX_VAULTS": str(vault_with_same_name_different_dirs)}),
                patch.object(db_module, "DB_PATH", temp_db_path),
            ):
                yield vault_with_same_name_different_dirs

    def test_rename_with_ambiguous_title_skips_ambiguous_links(self, vault_env_same_name):
        """When multiple files share a title, ambiguous title links are skipped."""
        result = rename(
            note_path="frontend/config", new_name="settings", vault=str(vault_env_same_name)
        )

        assert "error" not in result
        content = (vault_env_same_name / "index.md").read_text()

        # Path-based link should be updated
        assert "[[frontend/settings]]" in content
        assert "[[frontend/config]]" not in content

        # Other path-based links unchanged
        assert "[[backend/config]]" in content

        # Ambiguous title link should be SKIPPED (not updated)
        assert "[[config]]" in content

        # Should have warning about skipped links
        assert "skipped_ambiguous" in result or "warning" in result

    def test_rename_updates_only_specific_path_link(self, vault_env_same_name):
        """Renaming one file doesn't affect path links to other same-named files."""
        result = rename(
            note_path="backend/config", new_name="db-config", vault=str(vault_env_same_name)
        )

        assert "error" not in result
        content = (vault_env_same_name / "index.md").read_text()

        # Backend path link updated
        assert "[[backend/db-config]]" in content
        assert "[[backend/config]]" not in content

        # Frontend path link unchanged
        assert "[[frontend/config]]" in content

    @pytest.fixture
    def vault_with_case_variants(self):
        """Vault with files that have same name but different casing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)

            # Create two files with same name, different case
            # Note: This only works on case-sensitive filesystems (Linux)
            (vault_path / "note.md").write_text("# Lowercase Note")
            (vault_path / "Note.md").write_text("# Uppercase Note")

            # Links to both
            (vault_path / "index.md").write_text(
                "# Index\n"
                "Lowercase: [[note]]\n"
                "Uppercase: [[Note]]\n"
            )

            yield vault_path

    @pytest.fixture
    def vault_env_case_variants(self, vault_with_case_variants):
        """Set up vault with case-variant files."""
        with tempfile.TemporaryDirectory() as db_dir:
            temp_db_path = Path(db_dir) / "test.db"
            with (
                patch.dict(os.environ, {"MEMEX_VAULTS": str(vault_with_case_variants)}),
                patch.object(db_module, "DB_PATH", temp_db_path),
            ):
                yield vault_with_case_variants

    def test_rename_case_variant_requires_disambiguation(self, vault_env_case_variants):
        """When case variants exist, filename alone is ambiguous - require full path."""
        # Skip if filesystem is case-insensitive
        if not (vault_env_case_variants / "Note.md").exists():
            pytest.skip("Filesystem is case-insensitive")

        # Both should error because title "note" matches both note.md and Note.md
        result_lower = rename(note_path="note.md", new_name="document", vault=str(vault_env_case_variants))
        assert "error" in result_lower
        assert "Multiple notes with title" in result_lower["error"]

        result_upper = rename(note_path="Note.md", new_name="Document", vault=str(vault_env_case_variants))
        assert "error" in result_upper
        assert "Multiple notes with title" in result_upper["error"]

    def test_rename_path_exact_match_works_with_case_variants(self, vault_env_case_variants):
        """When path-based case variants exist, exact path match still works."""
        vault_path = vault_env_case_variants
        # Skip if filesystem is case-insensitive
        if not (vault_path / "Note.md").exists():
            pytest.skip("Filesystem is case-insensitive")

        # Create case variants in a subdirectory
        (vault_path / "docs").mkdir()
        (vault_path / "docs" / "guide.md").write_text("# Guide\nLowercase guide.")
        (vault_path / "docs" / "Guide.md").write_text("# Guide\nUppercase Guide.")

        # Exact path should work - user specified exactly which one
        result = rename(note_path="docs/guide.md", new_name="manual", vault=str(vault_path))
        assert "error" not in result
        assert result["old_path"] == "docs/guide.md"
        assert result["new_path"] == "docs/manual.md"

    def test_rename_path_ambiguous_without_exact_match(self, vault_env_case_variants):
        """Path-based lookup errors when no exact match and multiple case variants exist."""
        vault_path = vault_env_case_variants
        # Skip if filesystem is case-insensitive
        if not (vault_path / "Note.md").exists():
            pytest.skip("Filesystem is case-insensitive")

        # Create case variants in a subdirectory
        (vault_path / "docs").mkdir()
        (vault_path / "docs" / "Guide.md").write_text("# Guide\nUppercase Guide.")
        (vault_path / "docs" / "GUIDE.md").write_text("# GUIDE\nAll caps GUIDE.")

        # "docs/guide" doesn't match exactly, and there are multiple case variants
        result = rename(note_path="docs/guide", new_name="manual", vault=str(vault_path))
        assert "error" in result
        assert "Multiple notes match path" in result["error"]
