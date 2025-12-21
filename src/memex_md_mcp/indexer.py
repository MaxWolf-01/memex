"""File discovery and indexing orchestration."""

import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection

from memex_md_mcp.db import delete_note, get_indexed_mtimes, get_note_rowid, init_db, upsert_embedding, upsert_note
from memex_md_mcp.embeddings import embed_text
from memex_md_mcp.logging import get_logger
from memex_md_mcp.parser import parse_note

log = get_logger()


@dataclass
class IndexStats:
    """Statistics from an indexing run."""

    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.added + self.updated + self.deleted

    @property
    def total_in_vault(self) -> int:
        return self.added + self.updated + self.unchanged


def content_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()


def discover_files(vault_path: Path) -> dict[str, float]:
    """Find all .md files in vault, return {relative_path: mtime}.

    Excludes hidden directories (starting with '.') like .obsidian, .trash, .git.
    """
    files = {}
    for root, dirs, filenames in os.walk(vault_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for filename in filenames:
            if not filename.endswith(".md"):
                continue
            filepath = Path(root) / filename
            rel_path = str(filepath.relative_to(vault_path))
            files[rel_path] = filepath.stat().st_mtime
    return files


def index_vault(
    conn: Connection,
    vault_id: str,
    vault_path: Path,
    on_progress: Callable[[str], None] | None = None,
) -> IndexStats:
    """Index a single vault, updating only stale files.

    Args:
        conn: Database connection (must already have schema initialized)
        vault_id: Identifier for this vault (used in DB)
        vault_path: Absolute path to vault directory
        on_progress: Optional callback for progress messages
    """
    start_time = time.monotonic()
    stats = IndexStats()

    disk_files = discover_files(vault_path)
    indexed_mtimes = get_indexed_mtimes(conn, vault_id)

    disk_paths = set(disk_files.keys())
    indexed_paths = set(indexed_mtimes.keys())

    new_paths = disk_paths - indexed_paths
    deleted_paths = indexed_paths - disk_paths
    existing_paths = disk_paths & indexed_paths

    changed_paths = {p for p in existing_paths if disk_files[p] > indexed_mtimes[p]}
    unchanged_paths = existing_paths - changed_paths

    stats.unchanged = len(unchanged_paths)

    to_index = new_paths | changed_paths
    total = len(to_index)

    if total > 0 and on_progress:
        on_progress(f"Indexing {total} files in {vault_id}...")

    for i, rel_path in enumerate(sorted(to_index)):
        filepath = vault_path / rel_path
        filename = filepath.name

        try:
            note = parse_note(str(filepath), filename)
            mtime = disk_files[rel_path]
            chash = content_hash(note.content)
            upsert_note(conn, vault_id, rel_path, note, mtime, chash)

            rowid = get_note_rowid(conn, vault_id, rel_path)
            if rowid is not None:
                # Include title in embedding to handle empty notes and improve single-keyword queries; "#" might be more in-distribution for title, haven't benchmarked
                text_to_embed = f"# {note.title}\n{note.content}"
                embedding = embed_text(text_to_embed)
                upsert_embedding(conn, rowid, embedding)

            if rel_path in new_paths:
                stats.added += 1
            else:
                stats.updated += 1

        except Exception as e:
            stats.errors.append(f"{rel_path}: {e}")
            log.error("Index error in '%s': %s: %s", vault_id, rel_path, e)

        # Progress every ~10% for large vaults
        if on_progress and total >= 10 and (i + 1) % max(1, total // 10) == 0:
            on_progress(f"  {i + 1}/{total} indexed")

    # Delete removed files
    for rel_path in deleted_paths:
        delete_note(conn, vault_id, rel_path)
        stats.deleted += 1

    elapsed = time.monotonic() - start_time
    if stats.total_processed > 0:
        log.info(
            "Indexed '%s': +%d new, ~%d updated, -%d deleted (%d total) in %.2fs",
            vault_id,
            stats.added,
            stats.updated,
            stats.deleted,
            stats.total_in_vault,
            elapsed,
        )

    return stats


def index_all_vaults(
    conn: Connection,
    vaults: dict[str, Path],
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, IndexStats]:
    """Index all configured vaults.

    Args:
        conn: Database connection
        vaults: Mapping of vault_id -> vault_path
        on_progress: Optional callback for progress messages
    """
    init_db(conn)
    results = {}

    for vault_id, vault_path in vaults.items():
        if not vault_path.exists():
            if on_progress:
                on_progress(f"Vault not found: {vault_path}")
            results[vault_id] = IndexStats(errors=[f"Vault path does not exist: {vault_path}"])
            continue

        stats = index_vault(conn, vault_id, vault_path, on_progress)
        results[vault_id] = stats

        if on_progress and stats.total_processed > 0:
            on_progress(f"{vault_id}: +{stats.added} ~{stats.updated} -{stats.deleted} ({stats.unchanged} unchanged)")

    return results
