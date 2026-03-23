"""Tests for fuzzy find."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from memex_md.cli import do_find
from memex_md.config import Config, VaultConfig
from memex_md.find import find_notes

# ── Unit tests for the scoring engine ────────────────────────────────────────


NOTES = [
    ("k-NN.md", "k-NN", ["knn", "k-nearest neighbors"]),
    ("neural-ode.md", "neural-ode", ["neural ordinary differential equations"]),
    ("derivative.md", "derivative", []),
    ("model-context-protocol.md", "model-context-protocol", ["mcp"]),
    ("auth.md", "auth", ["authentication"]),
    ("math/linear-algebra.md", "linear-algebra", ["linalg"]),
    ("lstm.md", "lstm", ["LSTM", "long short-term memory"]),
    ("transformer.md", "transformer", ["attention is all you need"]),
    ("unrelated.md", "unrelated", []),
]


class TestScoring:
    def test_exact_title(self):
        results = find_notes(NOTES, "auth")
        assert results[0].path == "auth.md"

    def test_exact_alias(self):
        results = find_notes(NOTES, "knn")
        assert results[0].path == "k-NN.md"

    def test_exact_alias_mcp(self):
        results = find_notes(NOTES, "mcp")
        assert results[0].path == "model-context-protocol.md"

    def test_fuzzy_title(self):
        results = find_notes(NOTES, "derivatives")
        assert results[0].path == "derivative.md"

    def test_fuzzy_title_knn(self):
        """knn should find k-NN even without the alias (fuzzy on title)."""
        notes_no_alias = [("k-NN.md", "k-NN", [])] + NOTES[1:]
        results = find_notes(notes_no_alias, "knn")
        assert results[0].path == "k-NN.md"

    def test_substring_in_alias(self):
        results = find_notes(NOTES, "nearest neighbor")
        assert results[0].path == "k-NN.md"

    def test_path_match(self):
        results = find_notes(NOTES, "math")
        assert any(r.path == "math/linear-algebra.md" for r in results)

    def test_multi_word_any_match(self):
        """Multi-word: any part matching is enough to appear."""
        results = find_notes(NOTES, "neural transformer")
        paths = [r.path for r in results]
        assert "neural-ode.md" in paths
        assert "transformer.md" in paths

    def test_multi_word_both_match_ranks_higher(self):
        """Notes matching more parts should rank higher."""
        notes = [
            ("a.md", "neural transformer", []),
            ("b.md", "neural network", []),
            ("c.md", "transformer architecture", []),
        ]
        results = find_notes(notes, "neural transformer")
        assert results[0].path == "a.md"

    def test_no_match(self):
        results = find_notes(NOTES, "zzzzxyzzy")
        assert len(results) == 0

    def test_limit(self):
        results = find_notes(NOTES, "a", limit=3)
        assert len(results) <= 3

    def test_case_insensitive(self):
        results = find_notes(NOTES, "LSTM")
        assert results[0].path == "lstm.md"

    def test_empty_query(self):
        results = find_notes(NOTES, "")
        assert len(results) == 0

    def test_alias_ranks_above_fuzzy_path(self):
        """Exact alias match should beat a fuzzy path match."""
        results = find_notes(NOTES, "linalg")
        assert results[0].path == "math/linear-algebra.md"


# ── Integration tests via do_find ────────────────────────────────────────────


def _make_config(vault_path: Path, db_dir: Path, vault_name: str = "test") -> Config:
    return Config(
        default_model="none",
        vaults={vault_name: VaultConfig(name=vault_name, paths=[vault_path], model="none")},
    )


def _patch_config(config: Config, db_dir: Path):
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
def find_vault():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)
        (vault_path / "k-NN.md").write_text(
            "---\naliases: [knn, k-nearest neighbors]\n---\nk-nearest neighbors algorithm."
        )
        (vault_path / "neural-ode.md").write_text(
            "---\naliases: [neural ordinary differential equations]\n---\nNeural ODEs."
        )
        (vault_path / "auth.md").write_text("---\naliases: [authentication]\n---\nAuth notes.")
        (vault_path / "derivative.md").write_text("Derivatives and calculus.")
        yield vault_path


@pytest.fixture
def find_env(find_vault):
    with tempfile.TemporaryDirectory() as db_dir:
        db_path = Path(db_dir)
        config = _make_config(find_vault, db_path)
        patches = _patch_config(config, db_path)
        with patches[0], patches[1], patches[2], patches[3]:
            yield find_vault


class TestDoFind:
    def test_find_exact_alias(self, find_env):
        result = do_find(query="knn")
        assert "test" in result
        assert "k-NN.md" in result["test"]

    def test_find_fuzzy(self, find_env):
        result = do_find(query="derivatives")
        assert "test" in result
        assert "derivative.md" in result["test"]

    def test_find_no_match(self, find_env):
        result = do_find(query="zzzzxyzzy")
        assert "message" in result

    def test_find_unknown_vault(self, find_env):
        result = do_find(query="test", vault="nonexistent")
        assert "error" in result

    def test_find_no_vaults(self):
        config = Config()
        with patch("memex_md.cli.load_config", return_value=config):
            result = do_find(query="test")
            assert "error" in result
