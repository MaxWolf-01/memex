"""File discovery and indexing orchestration."""

import hashlib
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection

from memex_md.db import (
    delete_note,
    get_indexed_mtimes,
    get_notes_needing_embeddings,
    init_db,
    upsert_embedding,
    upsert_note,
)
from memex_md.logging import get_logger
from memex_md.parser import parse_note

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


def discover_files(vault_path: Path, ignore: list[str] | None = None) -> dict[str, float]:
    """Find all .md files in directory, return {relative_path: mtime}.

    Excludes hidden directories (starting with '.') like .obsidian, .trash, .git.
    Excludes directories and files matching ignore patterns (fnmatch).
    """
    from fnmatch import fnmatch

    ignore = ignore or []
    files = {}
    for root_dir, dirs, filenames in os.walk(vault_path):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".") and not any(fnmatch(d, pat) for pat in ignore)
        ]
        for filename in filenames:
            if not filename.endswith(".md"):
                continue
            if any(fnmatch(filename, pat) for pat in ignore):
                continue
            filepath = Path(root_dir) / filename
            rel_path = str(filepath.relative_to(vault_path))
            files[rel_path] = filepath.stat().st_mtime
    return files


def index_root(
    conn: Connection,
    root_path: Path,
    on_progress: Callable[[str], None] | None = None,
    ignore: list[str] | None = None,
) -> IndexStats:
    """Index a single root directory within a vault."""
    start_time = time.monotonic()
    stats = IndexStats()
    root_str = str(root_path)

    disk_files = discover_files(root_path, ignore=ignore)
    indexed_mtimes = get_indexed_mtimes(conn, root_str)

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
        on_progress(f"Indexing {total} files in {root_path}...")

    for i, rel_path in enumerate(sorted(to_index)):
        filepath = root_path / rel_path
        filename = filepath.name

        try:
            note = parse_note(str(filepath), filename)
            mtime = disk_files[rel_path]
            chash = content_hash(note.content)
            upsert_note(conn, root_str, rel_path, note, mtime, chash)

            if rel_path in new_paths:
                stats.added += 1
            else:
                stats.updated += 1

        except Exception as e:
            stats.errors.append(f"{rel_path}: {e}")
            log.error("Index error in '%s': %s: %s", root_str, rel_path, e)

        if on_progress and total >= 10 and (i + 1) % max(1, total // 10) == 0:
            on_progress(f"  {i + 1}/{total} indexed")

    for rel_path in deleted_paths:
        delete_note(conn, root_str, rel_path)
        stats.deleted += 1

    elapsed = time.monotonic() - start_time
    if stats.total_processed > 0:
        log.info(
            "Indexed '%s': +%d new, ~%d updated, -%d deleted (%d total) in %.2fs",
            root_str,
            stats.added,
            stats.updated,
            stats.deleted,
            stats.total_in_vault,
            elapsed,
        )

    return stats


def index_vault(
    conn: Connection,
    roots: list[Path],
    model_name: str | None = None,
    embedding_dim: int | None = None,
    on_progress: Callable[[str], None] | None = None,
    ignore: list[str] | None = None,
) -> IndexStats:
    """Index all roots in a vault. Handles DB init and embedding."""
    init_db(conn, model_name, embedding_dim)

    combined = IndexStats()
    for root_path in roots:
        if not root_path.exists():
            if on_progress:
                on_progress(f"Root not found: {root_path}")
            combined.errors.append(f"Root path does not exist: {root_path}")
            continue

        stats = index_root(conn, root_path, on_progress, ignore=ignore)
        combined.added += stats.added
        combined.updated += stats.updated
        combined.deleted += stats.deleted
        combined.unchanged += stats.unchanged
        combined.errors.extend(stats.errors)

    # Embed notes with missing or stale embeddings
    if model_name and model_name.lower() != "none":
        from memex_md.embeddings import embed_texts as _embed_texts

        needs = get_notes_needing_embeddings(conn)
        if needs:
            items = list(needs.values())
            n = len(items)
            chunk_size = 128
            log.info("Embedding %d notes...", n)
            print(f"Embedding {n} notes...", file=sys.stderr)

            for start in range(0, n, chunk_size):
                chunk = items[start : start + chunk_size]
                texts = [f"# {title}\n{content}" for (_rowid, _root, title, content, _chash) in chunk]
                embeddings = _embed_texts(texts, model_name)
                for i, (rowid, _root, _title, _content, chash) in enumerate(chunk):
                    upsert_embedding(conn, rowid, embeddings[i], chash)
                done = min(start + chunk_size, n)
                if done < n:
                    print(f"  {done}/{n} embedded", file=sys.stderr)

            log.info("Embedded %d notes.", n)

    return combined
