"""Configuration for memex: vault definitions, model settings, data paths."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "memex" / "config.toml"
DATA_DIR = Path.home() / ".local" / "share" / "memex-md"

DEFAULT_MODEL = "google/embeddinggemma-300m"


@dataclass
class VaultConfig:
    name: str
    paths: list[Path]
    model: str = DEFAULT_MODEL

    @property
    def semantic_enabled(self) -> bool:
        return bool(self.model) and self.model.lower() != "none"


@dataclass
class Config:
    default_model: str = DEFAULT_MODEL
    vaults: dict[str, VaultConfig] = field(default_factory=dict)


def load_config() -> Config:
    """Load config from TOML file. Returns empty config if file doesn't exist."""
    if not CONFIG_PATH.exists():
        return Config()

    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)

    default_model = raw.get("defaults", {}).get("model", DEFAULT_MODEL)
    config = Config(default_model=default_model)

    for name, vault_data in raw.get("vaults", {}).items():
        raw_paths = vault_data.get("paths", [])
        paths = [Path(p).expanduser().resolve() for p in raw_paths]
        model = vault_data.get("model", default_model)
        config.vaults[name] = VaultConfig(name=name, paths=paths, model=model)

    return config


def save_config(config: Config) -> None:
    """Write config to TOML file."""
    lines = ["[defaults]", f'model = "{config.default_model}"', ""]

    for name in sorted(config.vaults):
        vault = config.vaults[name]
        lines.append(f"[vaults.{name}]")
        paths_str = ", ".join(f'"{p}"' for p in vault.paths)
        lines.append(f"paths = [{paths_str}]")
        if vault.model != config.default_model:
            lines.append(f'model = "{vault.model}"')
        lines.append("")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("\n".join(lines))


def db_path_for_vault(vault_name: str) -> Path:
    """Get the database path for a vault."""
    return DATA_DIR / vault_name / "index.db"
