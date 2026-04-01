"""Configuration for memex: vault definitions, model settings, data paths."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

CONFIG_PATH = _config_home / "memex" / "config.toml"
DATA_DIR = _data_home / "memex"

# One-time migration from old data dir name
_old_data_dir = _data_home / "memex-md"
if _old_data_dir.is_dir() and not DATA_DIR.exists():
    _old_data_dir.rename(DATA_DIR)

DEFAULT_MODEL = "google/embeddinggemma-300m"
DEFAULT_IGNORE: list[str] = [
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "vendor",
    "site-packages",
]


@dataclass
class VaultConfig:
    name: str
    paths: list[Path]
    model: str = DEFAULT_MODEL
    ignore: list[str] = field(default_factory=list)

    @property
    def semantic_enabled(self) -> bool:
        return bool(self.model) and self.model.lower() != "none"


@dataclass
class Config:
    default_model: str = DEFAULT_MODEL
    ignore: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    vaults: dict[str, VaultConfig] = field(default_factory=dict)


def load_config() -> Config:
    """Load config from TOML file. Returns empty config if file doesn't exist."""
    if not CONFIG_PATH.exists():
        return Config()

    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)

    defaults = raw.get("defaults", {})
    default_model = defaults.get("model", DEFAULT_MODEL)
    config = Config(default_model=default_model)
    if "ignore" in defaults:
        config.ignore = list(defaults["ignore"])

    for name, vault_data in raw.get("vaults", {}).items():
        raw_paths = vault_data.get("paths", [])
        paths = [Path(p).expanduser().resolve() for p in raw_paths]
        model = vault_data.get("model", default_model)
        ignore = list(vault_data.get("ignore", []))
        config.vaults[name] = VaultConfig(name=name, paths=paths, model=model, ignore=ignore)

    return config


def save_config(config: Config) -> None:
    """Write config to TOML file."""
    lines = ["[defaults]", f'model = "{config.default_model}"']
    if config.ignore != DEFAULT_IGNORE:
        ignore_str = ", ".join(f'"{p}"' for p in config.ignore)
        lines.append(f"ignore = [{ignore_str}]")
    lines.append("")

    for name in sorted(config.vaults):
        vault = config.vaults[name]
        lines.append(f"[vaults.{name}]")
        paths_str = ", ".join(f'"{p}"' for p in vault.paths)
        lines.append(f"paths = [{paths_str}]")
        if vault.model != config.default_model:
            lines.append(f'model = "{vault.model}"')
        if vault.ignore:
            ignore_str = ", ".join(f'"{p}"' for p in vault.ignore)
            lines.append(f"ignore = [{ignore_str}]")
        lines.append("")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("\n".join(lines))


def db_path_for_vault(vault_name: str) -> Path:
    """Get the database path for a vault."""
    return DATA_DIR / vault_name / "index.db"
