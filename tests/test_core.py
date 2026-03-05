"""Tests for CLI business logic."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from memex_md.cli import do_explore, do_rename, do_search
from memex_md.config import DEFAULT_IGNORE, Config, VaultConfig, load_config, save_config


def _make_config(vault_path: Path, db_dir: Path, vault_name: str = "test", model: str = "none") -> Config:
    """Create a test config pointing at a temp vault with isolated DB."""
    config = Config(
        default_model=model,
        vaults={vault_name: VaultConfig(name=vault_name, paths=[vault_path], model=model)},
    )
    return config


def _patch_config(config: Config, db_dir: Path):
    """Patch load_config and db_path_for_vault for test isolation."""
    from memex_md import config as config_module

    def fake_db_path(vault_name: str) -> Path:
        return db_dir / vault_name / "index.db"

    return (
        patch.object(config_module, "load_config", return_value=config),
        patch("memex_md.cli.load_config", return_value=config),
        patch("memex_md.cli.db_path_for_vault", side_effect=fake_db_path),
        patch("memex_md.config.db_path_for_vault", side_effect=fake_db_path),
    )


@pytest.fixture
def temp_vault():
    """Create a temporary vault with test notes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)

        (vault_path / "python.md").write_text(
            "---\naliases: [py]\ntags: [programming]\n---\nPython is a programming language."
        )
        (vault_path / "rust.md").write_text(
            "---\naliases: [rs]\ntags: [programming]\n---\nRust is a systems programming language."
        )
        (vault_path / "javascript.md").write_text("---\ntags: [programming, web]\n---\nJavaScript runs in browsers.")
        (vault_path / "auth.md").write_text(
            "---\ntags: [security]\n---\nWe decided to use OAuth for authentication. JWT tokens for sessions."
        )
        (vault_path / "database.md").write_text("---\ntags: [backend]\n---\nUsing PostgreSQL for the main database.")

        yield vault_path


@pytest.fixture
def vault_env(temp_vault):
    """Set up config-based test vault with isolated DB (semantic disabled)."""
    with tempfile.TemporaryDirectory() as db_dir:
        db_path = Path(db_dir)
        config = _make_config(temp_vault, db_path)
        patches = _patch_config(config, db_path)
        with patches[0], patches[1], patches[2], patches[3]:
            yield temp_vault


class TestSearch:
    def test_semantic_search(self, vault_env):
        """Semantic search returns results (requires model, skipped with model=none)."""
        result = do_search(query="What programming language is good for systems?", limit=5)
        # With model=none, no semantic results
        assert "message" in result or "test" in result

    def test_search_unknown_vault(self, vault_env):
        """Search with unknown vault returns error."""
        result = do_search(query="test", vault="nonexistent")
        assert "error" in result
        assert "Unknown vault" in result["error"]

    def test_search_specific_vault(self, vault_env):
        """Search with valid vault name works."""
        result = do_search(query="programming", vault="test")
        # With model=none, no results but no error either
        assert "error" not in result

    def test_search_no_vaults_configured(self):
        """Search with no vaults configured returns error."""
        config = Config()
        with (
            patch("memex_md.cli.load_config", return_value=config),
        ):
            result = do_search(query="test")
            assert "error" in result
            assert "No vaults configured" in result["error"]


class TestSearchPagination:
    def test_page_beyond_results_empty(self, vault_env):
        """Page far beyond results returns no results message."""
        result = do_search(query="programming", limit=2, page=100)
        assert "message" in result or all(len(v) == 0 for v in result.values() if isinstance(v, list))


class TestExplore:
    def test_explore_with_extension(self, vault_env):
        """Explore works with .md extension."""
        result = do_explore(note_path="auth.md", vault="test")
        assert "error" not in result
        assert result["note"]["path"] == "auth.md"

    def test_explore_without_extension(self, vault_env):
        """Explore works without .md extension."""
        result = do_explore(note_path="auth", vault="test")
        assert "error" not in result
        assert result["note"]["path"] == "auth.md"

    def test_explore_nonexistent(self, vault_env):
        """Explore returns error for nonexistent note."""
        result = do_explore(note_path="nonexistent", vault="test")
        assert "error" in result

    def test_explore_by_title_unique(self, vault_env):
        """Explore works with just title if unique in vault."""
        result = do_explore(note_path="auth", vault="test")
        assert "error" not in result
        assert result["note"]["path"] == "auth.md"

    def test_explore_by_title_in_subdir(self, vault_env):
        """Explore by title finds file in subdirectory."""
        subdir = vault_env / "subdir"
        subdir.mkdir()
        (subdir / "unique-note.md").write_text("# Unique Note\nContent here.")

        result = do_explore(note_path="unique-note", vault="test")
        assert "error" not in result
        assert result["note"]["path"] == "subdir/unique-note.md"

    def test_explore_by_title_ambiguous_error(self, vault_env):
        """Explore by title errors when multiple files have same title."""
        subdir = vault_env / "subdir"
        subdir.mkdir(exist_ok=True)
        (subdir / "auth.md").write_text("# Auth\nAnother auth file.")

        result = do_explore(note_path="auth", vault="test")
        assert "error" in result
        assert "Multiple notes" in result["error"]

    def test_explore_unknown_vault(self, vault_env):
        """Explore with unknown vault returns error."""
        result = do_explore(note_path="auth", vault="nonexistent")
        assert "error" in result
        assert "Unknown vault" in result["error"]

    def test_explore_similar_empty_when_semantic_disabled(self, vault_env):
        """Explore returns empty similar list when semantic disabled."""
        result = do_explore(note_path="auth", vault="test")
        assert "error" not in result
        assert result["similar"] == []


class TestRename:
    @pytest.fixture
    def vault_with_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            (vault_path / "backend.md").write_text("# Backend\nSee also [[auth]] and [[database]].")
            (vault_path / "auth.md").write_text("# Auth\nAuthentication module. Used by [[backend]].")
            (vault_path / "database.md").write_text("# Database\nPostgreSQL setup.")
            (vault_path / "overview.md").write_text("# Overview\nMain components: [[backend]], [[auth]], [[database]].")
            yield vault_path

    @pytest.fixture
    def vault_env_links(self, vault_with_links):
        with tempfile.TemporaryDirectory() as db_dir:
            db_path = Path(db_dir)
            config = _make_config(vault_with_links, db_path)
            patches = _patch_config(config, db_path)
            with patches[0], patches[1], patches[2], patches[3]:
                yield vault_with_links

    def test_rename_simple(self, vault_env_links):
        result = do_rename(note_path="database", new_name="postgres", vault="test")
        assert "error" not in result
        assert result["old_path"] == "database.md"
        assert result["new_path"] == "postgres.md"
        assert (vault_env_links / "postgres.md").exists()
        assert not (vault_env_links / "database.md").exists()

    def test_rename_updates_backlinks(self, vault_env_links):
        result = do_rename(note_path="auth.md", new_name="authentication", vault="test")
        assert "error" not in result
        assert result["updated_count"] >= 1
        backend_content = (vault_env_links / "backend.md").read_text()
        assert "[[authentication]]" in backend_content
        assert "[[auth]]" not in backend_content
        overview_content = (vault_env_links / "overview.md").read_text()
        assert "[[authentication]]" in overview_content

    def test_rename_target_exists_error(self, vault_env_links):
        result = do_rename(note_path="auth", new_name="backend", vault="test")
        assert "error" in result
        assert "exists" in result["error"].lower()

    def test_rename_source_not_found(self, vault_env_links):
        result = do_rename(note_path="nonexistent", new_name="something", vault="test")
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_rename_by_title_resolves_subdirectory(self, vault_env_links):
        vault_path = vault_env_links
        (vault_path / "docs").mkdir()
        (vault_path / "docs" / "guide.md").write_text("# Guide\nSome documentation.")
        result = do_rename(note_path="guide", new_name="manual", vault="test")
        assert "error" not in result
        assert result["old_path"] == "docs/guide.md"
        assert result["new_path"] == "docs/manual.md"
        assert (vault_path / "docs" / "manual.md").exists()

    @pytest.fixture
    def vault_with_complex_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
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
            (vault_path / "subdir").mkdir()
            (vault_path / "subdir" / "nested.md").write_text("# Nested\nLinks to [[target]] from subdir.")
            yield vault_path

    @pytest.fixture
    def vault_env_complex(self, vault_with_complex_links):
        with tempfile.TemporaryDirectory() as db_dir:
            db_path = Path(db_dir)
            config = _make_config(vault_with_complex_links, db_path)
            patches = _patch_config(config, db_path)
            with patches[0], patches[1], patches[2], patches[3]:
                yield vault_with_complex_links

    def test_rename_preserves_alias(self, vault_env_complex):
        result = do_rename(note_path="target", new_name="destination", vault="test")
        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[destination|Display Name]]" in content

    def test_rename_preserves_heading(self, vault_env_complex):
        result = do_rename(note_path="target", new_name="destination", vault="test")
        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[destination#section]]" in content

    def test_rename_preserves_heading_and_alias(self, vault_env_complex):
        result = do_rename(note_path="target", new_name="destination", vault="test")
        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[destination#section|Section Link]]" in content

    def test_rename_case_insensitive_matching(self, vault_env_complex):
        result = do_rename(note_path="target", new_name="destination", vault="test")
        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[target]]" not in content
        assert "[[Target]]" not in content
        assert content.count("[[destination") >= 2

    def test_rename_updates_links_in_subdirs(self, vault_env_complex):
        result = do_rename(note_path="target", new_name="destination", vault="test")
        assert "error" not in result
        nested_content = (vault_env_complex / "subdir" / "nested.md").read_text()
        assert "[[destination]]" in nested_content

    def test_rename_preserves_block_reference(self, vault_env_complex):
        result = do_rename(note_path="target", new_name="destination", vault="test")
        assert "error" not in result
        content = (vault_env_complex / "index.md").read_text()
        assert "[[destination#^abc123]]" in content
        assert "[[destination#^abc123|Block Link]]" in content


class TestRenameEdgeCases:
    @pytest.fixture
    def vault_with_path_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            (vault_path / "docs").mkdir()
            (vault_path / "docs" / "guide.md").write_text("# Guide\nThis is a guide.")
            (vault_path / "index.md").write_text(
                "# Index\nTitle link: [[guide]]\nPath link: [[docs/guide]]\nPath with alias: [[docs/guide|The Guide]]\n"
            )
            yield vault_path

    @pytest.fixture
    def vault_env_path_links(self, vault_with_path_links):
        with tempfile.TemporaryDirectory() as db_dir:
            db_path = Path(db_dir)
            config = _make_config(vault_with_path_links, db_path)
            patches = _patch_config(config, db_path)
            with patches[0], patches[1], patches[2], patches[3]:
                yield vault_with_path_links

    def test_rename_updates_path_based_links(self, vault_env_path_links):
        result = do_rename(note_path="docs/guide", new_name="manual", vault="test")
        assert "error" not in result
        content = (vault_env_path_links / "index.md").read_text()
        assert "[[docs/manual]]" in content
        assert "[[docs/manual|The Guide]]" in content
        assert "[[manual]]" in content
        assert "[[guide]]" not in content
        assert "[[docs/guide]]" not in content

    @pytest.fixture
    def vault_with_same_name_different_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            (vault_path / "frontend").mkdir()
            (vault_path / "backend").mkdir()
            (vault_path / "frontend" / "config.md").write_text("# Frontend Config")
            (vault_path / "backend" / "config.md").write_text("# Backend Config")
            (vault_path / "index.md").write_text(
                "# Index\n"
                "Ambiguous: [[config]]\n"
                "Frontend specific: [[frontend/config]]\n"
                "Backend specific: [[backend/config]]\n"
            )
            yield vault_path

    @pytest.fixture
    def vault_env_same_name(self, vault_with_same_name_different_dirs):
        with tempfile.TemporaryDirectory() as db_dir:
            db_path = Path(db_dir)
            config = _make_config(vault_with_same_name_different_dirs, db_path)
            patches = _patch_config(config, db_path)
            with patches[0], patches[1], patches[2], patches[3]:
                yield vault_with_same_name_different_dirs

    def test_rename_with_ambiguous_title_skips_ambiguous_links(self, vault_env_same_name):
        result = do_rename(note_path="frontend/config", new_name="settings", vault="test")
        assert "error" not in result
        content = (vault_env_same_name / "index.md").read_text()
        assert "[[frontend/settings]]" in content
        assert "[[frontend/config]]" not in content
        assert "[[backend/config]]" in content
        assert "[[config]]" in content
        assert "skipped_ambiguous" in result or "warning" in result

    def test_rename_updates_only_specific_path_link(self, vault_env_same_name):
        result = do_rename(note_path="backend/config", new_name="db-config", vault="test")
        assert "error" not in result
        content = (vault_env_same_name / "index.md").read_text()
        assert "[[backend/db-config]]" in content
        assert "[[backend/config]]" not in content
        assert "[[frontend/config]]" in content

    @pytest.fixture
    def vault_with_case_variants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            (vault_path / "note.md").write_text("# Lowercase Note")
            (vault_path / "Note.md").write_text("# Uppercase Note")
            (vault_path / "index.md").write_text("# Index\nLowercase: [[note]]\nUppercase: [[Note]]\n")
            yield vault_path

    @pytest.fixture
    def vault_env_case_variants(self, vault_with_case_variants):
        with tempfile.TemporaryDirectory() as db_dir:
            db_path = Path(db_dir)
            config = _make_config(vault_with_case_variants, db_path)
            patches = _patch_config(config, db_path)
            with patches[0], patches[1], patches[2], patches[3]:
                yield vault_with_case_variants

    def test_rename_case_variant_requires_disambiguation(self, vault_env_case_variants):
        if not (vault_env_case_variants / "Note.md").exists():
            pytest.skip("Filesystem is case-insensitive")
        result_lower = do_rename(note_path="note.md", new_name="document", vault="test")
        assert "error" in result_lower
        assert "Multiple notes with title" in result_lower["error"]

    def test_rename_path_exact_match_works_with_case_variants(self, vault_env_case_variants):
        vault_path = vault_env_case_variants
        if not (vault_path / "Note.md").exists():
            pytest.skip("Filesystem is case-insensitive")
        (vault_path / "docs").mkdir()
        (vault_path / "docs" / "guide.md").write_text("# Guide\nLowercase guide.")
        (vault_path / "docs" / "Guide.md").write_text("# Guide\nUppercase Guide.")
        result = do_rename(note_path="docs/guide.md", new_name="manual", vault="test")
        assert "error" not in result
        assert result["old_path"] == "docs/guide.md"
        assert result["new_path"] == "docs/manual.md"

    def test_rename_path_ambiguous_without_exact_match(self, vault_env_case_variants):
        vault_path = vault_env_case_variants
        if not (vault_path / "Note.md").exists():
            pytest.skip("Filesystem is case-insensitive")
        (vault_path / "docs").mkdir()
        (vault_path / "docs" / "Guide.md").write_text("# Guide\nUppercase Guide.")
        (vault_path / "docs" / "GUIDE.md").write_text("# GUIDE\nAll caps GUIDE.")
        result = do_rename(note_path="docs/guide", new_name="manual", vault="test")
        assert "error" in result
        assert "Multiple notes match path" in result["error"]


class TestConfigIgnore:
    def test_default_ignore_not_written_to_toml(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config = Config()
        with patch("memex_md.config.CONFIG_PATH", config_path):
            save_config(config)
        content = config_path.read_text()
        assert "ignore" not in content

    def test_custom_global_ignore_written(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config = Config(ignore=["custom_dir", "*.tmp"])
        with patch("memex_md.config.CONFIG_PATH", config_path):
            save_config(config)
        content = config_path.read_text()
        assert '"custom_dir"' in content
        assert '"*.tmp"' in content

    def test_per_vault_ignore_written(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config = Config(
            vaults={"test": VaultConfig(name="test", paths=[tmp_path], ignore=["*.generated.md"])},
        )
        with patch("memex_md.config.CONFIG_PATH", config_path):
            save_config(config)
        content = config_path.read_text()
        assert '"*.generated.md"' in content

    def test_config_roundtrip_with_ignore(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config = Config(
            ignore=["custom_dir"],
            vaults={"test": VaultConfig(name="test", paths=[tmp_path], ignore=["*.tmp"])},
        )
        with patch("memex_md.config.CONFIG_PATH", config_path):
            save_config(config)
            loaded = load_config()
        assert loaded.ignore == ["custom_dir"]
        assert loaded.vaults["test"].ignore == ["*.tmp"]

    def test_default_ignore_preserved_when_not_in_toml(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config = Config()
        with patch("memex_md.config.CONFIG_PATH", config_path):
            save_config(config)
            loaded = load_config()
        assert loaded.ignore == DEFAULT_IGNORE
