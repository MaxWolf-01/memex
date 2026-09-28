"""Tests for the embedding device setting: config round trip and model loading."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memex_md import embeddings
from memex_md.config import load_config, save_config


@pytest.fixture
def config_path(tmp_path: Path):
    path = tmp_path / "config.toml"
    with patch("memex_md.config.CONFIG_PATH", path):
        yield path


class TestDeviceConfig:
    def test_device_read_from_defaults(self, config_path: Path):
        config_path.write_text('[defaults]\nmodel = "some/model"\ndevice = "cpu"\n')
        assert load_config().device == "cpu"

    def test_device_absent_means_auto(self, config_path: Path):
        config_path.write_text('[defaults]\nmodel = "some/model"\n')
        assert load_config().device is None

    def test_rewrite_keeps_device(self, config_path: Path):
        """vault:add and vault:remove rewrite the whole file; the device must survive that."""
        config_path.write_text('[defaults]\nmodel = "some/model"\ndevice = "cpu"\n')
        save_config(load_config())
        assert load_config().device == "cpu"

    def test_rewrite_without_device_adds_none(self, config_path: Path):
        config_path.write_text('[defaults]\nmodel = "some/model"\n')
        save_config(load_config())
        assert "device" not in config_path.read_text()


class TestModelDevice:
    @pytest.fixture(autouse=True)
    def fresh_cache(self):
        with patch.object(embeddings, "_model", None), patch.object(embeddings, "_model_key", None):
            yield

    def test_device_reaches_sentence_transformers(self):
        with patch("sentence_transformers.SentenceTransformer") as st:
            embeddings.get_model("some/model", "cpu")
        st.assert_called_once_with("some/model", device="cpu")

    def test_device_change_reloads(self):
        with patch("sentence_transformers.SentenceTransformer", side_effect=lambda *a, **k: MagicMock()) as st:
            first = embeddings.get_model("some/model", "cpu")
            assert embeddings.get_model("some/model", "cpu") is first
            embeddings.get_model("some/model", "cuda")
        assert [c.kwargs["device"] for c in st.call_args_list] == ["cpu", "cuda"]
