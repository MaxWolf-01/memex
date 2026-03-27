"""SQLite database for note indexing with vector search. One DB per vault."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import sqlite_vec

if TYPE_CHECKING:
    import numpy as np

from memex_md.parser import ParsedNote


@dataclass
class IndexedNote:
    """A note as stored in the database."""

    root: str  # absolute path of the source directory
    path: str  # relative path within root
    title: str
    aliases: list[str]
    tags: list[str]
    content: str
    mtime: float
    content_hash: str


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a database connection with sqlite-vec loaded."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    root TEXT NOT NULL,
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    content TEXT NOT NULL DEFAULT '',
    mtime REAL NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    embedding_hash TEXT,
    PRIMARY KEY (root, path)
);

CREATE INDEX IF NOT EXISTS idx_notes_root ON notes(root);
CREATE INDEX IF NOT EXISTS idx_notes_mtime ON notes(mtime);

CREATE TABLE IF NOT EXISTS wikilinks (
    source_root TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_raw TEXT NOT NULL,
    target_path TEXT,
    FOREIGN KEY (source_root, source_path) REFERENCES notes(root, path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wikilinks_source ON wikilinks(source_root, source_path);
CREATE INDEX IF NOT EXISTS idx_wikilinks_target ON wikilinks(target_path);
"""


def _get_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))


def init_db(conn: sqlite3.Connection, model_name: str | None = None, embedding_dim: int | None = None) -> None:
    """Initialize database schema. Handles model changes by dropping stale embeddings."""
    conn.executescript(SCHEMA)

    if model_name and embedding_dim:
        stored_model = _get_metadata(conn, "model")
        stored_dim = _get_metadata(conn, "embedding_dim")

        model_changed = stored_model is not None and stored_model != model_name
        dim_changed = stored_dim is not None and stored_dim != str(embedding_dim)

        if model_changed or dim_changed:
            conn.execute("DROP TABLE IF EXISTS notes_vec")
            conn.execute("UPDATE notes SET embedding_hash = NULL")

        _set_metadata(conn, "model", model_name)
        _set_metadata(conn, "embedding_dim", str(embedding_dim))

        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec USING vec0(
                note_rowid INTEGER PRIMARY KEY,
                embedding float[{embedding_dim}] distance_metric=cosine
            )
        """)

    conn.commit()


def _row_to_note(row: sqlite3.Row) -> IndexedNote:
    return IndexedNote(
        root=row["root"],
        path=row["path"],
        title=row["title"],
        aliases=json.loads(row["aliases"]),
        tags=json.loads(row["tags"]),
        content=row["content"],
        mtime=row["mtime"],
        content_hash=row["content_hash"],
    )


def upsert_note(
    conn: sqlite3.Connection,
    root: str,
    path: str,
    note: ParsedNote,
    mtime: float,
    content_hash: str,
) -> None:
    """Insert or update a note and its wikilinks."""
    aliases_json = json.dumps(note.aliases)
    tags_json = json.dumps(note.tags)

    conn.execute(
        """
        INSERT INTO notes (root, path, title, aliases, tags, content, mtime, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(root, path) DO UPDATE SET
            title = excluded.title,
            aliases = excluded.aliases,
            tags = excluded.tags,
            content = excluded.content,
            mtime = excluded.mtime,
            content_hash = excluded.content_hash
        """,
        (root, path, note.title, aliases_json, tags_json, note.content, mtime, content_hash),
    )

    conn.execute("DELETE FROM wikilinks WHERE source_root = ? AND source_path = ?", (root, path))
    if note.wikilinks:
        conn.executemany(
            "INSERT INTO wikilinks (source_root, source_path, target_raw, target_path) VALUES (?, ?, ?, NULL)",
            [(root, path, target) for target in note.wikilinks],
        )

    conn.commit()


def delete_note(conn: sqlite3.Connection, root: str, path: str) -> None:
    """Delete a note (wikilinks cascade automatically)."""
    conn.execute("DELETE FROM notes WHERE root = ? AND path = ?", (root, path))
    conn.commit()


def delete_root(conn: sqlite3.Connection, root: str) -> int:
    """Delete all notes for a root directory. Returns count of deleted notes."""
    cursor = conn.execute("DELETE FROM notes WHERE root = ?", (root,))
    conn.commit()
    return cursor.rowcount


def get_note(conn: sqlite3.Connection, path: str, root: str | None = None) -> IndexedNote | None:
    """Retrieve a note by path. Searches all roots if root is None. Extension .md is optional."""
    if root:
        row = conn.execute("SELECT * FROM notes WHERE path = ? AND root = ?", (path, root)).fetchone()
        if not row and not path.endswith(".md"):
            row = conn.execute("SELECT * FROM notes WHERE path = ? AND root = ?", (path + ".md", root)).fetchone()
    else:
        row = conn.execute("SELECT * FROM notes WHERE path = ?", (path,)).fetchone()
        if not row and not path.endswith(".md"):
            row = conn.execute("SELECT * FROM notes WHERE path = ?", (path + ".md",)).fetchone()

    return _row_to_note(row) if row else None


def get_indexed_mtimes(conn: sqlite3.Connection, root: str) -> dict[str, float]:
    """Get mtime for all notes under a root. For staleness checking."""
    rows = conn.execute("SELECT path, mtime FROM notes WHERE root = ?", (root,)).fetchall()
    return {row["path"]: row["mtime"] for row in rows}


def get_notes_needing_embeddings(
    conn: sqlite3.Connection, root: str | None = None
) -> dict[str, tuple[int, str, str, str, str]]:
    """Get notes with missing or stale embeddings.

    Returns {root+path: (rowid, root, title, content, content_hash)}.
    """
    if root:
        rows = conn.execute(
            """SELECT rowid, root, path, title, content, content_hash FROM notes
               WHERE root = ? AND (embedding_hash IS NULL OR embedding_hash != content_hash)""",
            (root,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT rowid, root, path, title, content, content_hash FROM notes WHERE embedding_hash IS NULL OR embedding_hash != content_hash"
        ).fetchall()

    return {
        f"{row['root']}/{row['path']}": (row["rowid"], row["root"], row["title"], row["content"], row["content_hash"])
        for row in rows
    }


def get_outlinks(conn: sqlite3.Connection, root: str, path: str) -> list[tuple[str, list[str]]]:
    """Get all wikilink targets from a note. Extension .md is optional."""
    rows = conn.execute(
        "SELECT target_raw FROM wikilinks WHERE source_root = ? AND source_path = ?",
        (root, path),
    ).fetchall()
    if not rows and not path.endswith(".md"):
        rows = conn.execute(
            "SELECT target_raw FROM wikilinks WHERE source_root = ? AND source_path = ?",
            (root, path + ".md"),
        ).fetchall()

    results = []
    for row in rows:
        target = row["target_raw"]
        resolved = resolve_wikilink(conn, target)
        results.append((target, resolved))
    return results


def resolve_wikilink(conn: sqlite3.Connection, target: str) -> list[str]:
    """Resolve a wikilink target to note path(s). Case-insensitive title match across all roots.

    Results ranked by: exact case match first, then shallowest path, then alphabetical.
    """
    rows = conn.execute(
        "SELECT path, title FROM notes WHERE LOWER(title) = LOWER(?)",
        (target,),
    ).fetchall()
    if len(rows) <= 1:
        return [row["path"] for row in rows]

    def sort_key(row: sqlite3.Row) -> tuple[int, int, str]:
        exact_case = 0 if row["title"] == target else 1
        depth = row["path"].count("/")
        return (exact_case, depth, row["path"].lower())

    return [row["path"] for row in sorted(rows, key=sort_key)]


def get_backlinks(conn: sqlite3.Connection, note_name: str) -> list[str]:
    """Find all notes that link TO the given note by title (case-insensitive)."""
    rows = conn.execute(
        "SELECT DISTINCT source_path FROM wikilinks WHERE LOWER(target_raw) = LOWER(?)",
        (note_name,),
    ).fetchall()
    return [row["source_path"] for row in rows]


def get_files_with_title(conn: sqlite3.Connection, title: str) -> list[str]:
    """Find all files with the given title (case-insensitive). For ambiguity detection."""
    rows = conn.execute(
        "SELECT path FROM notes WHERE LOWER(title) = LOWER(?)",
        (title,),
    ).fetchall()
    return [row["path"] for row in rows]


def get_files_with_path(conn: sqlite3.Connection, path_without_ext: str) -> list[str]:
    """Find all files with the given path (case-insensitive, without .md). For ambiguity detection."""
    rows = conn.execute(
        "SELECT path FROM notes WHERE LOWER(path) = LOWER(?)",
        (path_without_ext + ".md",),
    ).fetchall()
    return [row["path"] for row in rows]


def find_links_to_note(conn: sqlite3.Connection, note_path: str) -> list[tuple[str, str, str]]:
    """Find all wikilinks that resolve to the given note.

    Returns list of (source_path, target_raw, link_type) tuples where link_type is:
    - "path": Link uses path (e.g., [[subdir/note]])
    - "path_ambiguous": Path link but multiple files match (different case)
    - "title": Link uses title (e.g., [[note]])
    - "title_ambiguous": Title link but multiple files share this title
    """
    title = Path(note_path).stem
    path_without_ext = note_path.removesuffix(".md")

    results = []

    path_links = conn.execute(
        "SELECT source_path, target_raw FROM wikilinks WHERE LOWER(target_raw) = LOWER(?)",
        (path_without_ext,),
    ).fetchall()

    files_with_path = get_files_with_path(conn, path_without_ext)
    is_path_ambiguous = len(files_with_path) > 1

    for row in path_links:
        link_type = "path_ambiguous" if is_path_ambiguous else "path"
        results.append((row["source_path"], row["target_raw"], link_type))

    title_links = conn.execute(
        """SELECT source_path, target_raw FROM wikilinks
           WHERE LOWER(target_raw) = LOWER(?) AND target_raw NOT LIKE '%/%'""",
        (title,),
    ).fetchall()

    files_with_title = get_files_with_title(conn, title)
    is_title_ambiguous = len(files_with_title) > 1

    for row in title_links:
        link_type = "title_ambiguous" if is_title_ambiguous else "title"
        results.append((row["source_path"], row["target_raw"], link_type))

    return results


def upsert_embedding(conn: sqlite3.Connection, note_rowid: int, embedding: np.ndarray, content_hash: str) -> None:
    """Insert or update embedding for a note and mark it as up-to-date."""
    import numpy as np

    conn.execute("DELETE FROM notes_vec WHERE note_rowid = ?", (note_rowid,))
    conn.execute(
        "INSERT INTO notes_vec (note_rowid, embedding) VALUES (?, ?)",
        (note_rowid, embedding.astype(np.float32)),
    )
    conn.execute("UPDATE notes SET embedding_hash = ? WHERE rowid = ?", (content_hash, note_rowid))
    conn.commit()


def get_note_rowid(conn: sqlite3.Connection, path: str) -> int | None:
    """Get the rowid for a note by path. Extension .md is optional."""
    row = conn.execute("SELECT rowid FROM notes WHERE path = ?", (path,)).fetchone()
    if not row and not path.endswith(".md"):
        row = conn.execute("SELECT rowid FROM notes WHERE path = ?", (path + ".md",)).fetchone()
    return row[0] if row else None


def get_note_embedding(conn: sqlite3.Connection, path: str) -> np.ndarray | None:
    """Get the embedding vector for a note."""
    import numpy as np

    rowid = get_note_rowid(conn, path)
    if rowid is None:
        return None
    row = conn.execute("SELECT embedding FROM notes_vec WHERE note_rowid = ?", (rowid,)).fetchone()
    return np.frombuffer(row[0], dtype=np.float32) if row else None


def get_all_findable(conn: sqlite3.Connection) -> list[tuple[str, str, list[str]]]:
    """Get (path, title, aliases) for all notes. For fuzzy find."""
    rows = conn.execute("SELECT path, title, aliases FROM notes").fetchall()
    return [(row["path"], row["title"], json.loads(row["aliases"])) for row in rows]


def search_semantic(
    conn: sqlite3.Connection, query_embedding: np.ndarray, limit: int = 10
) -> list[tuple[IndexedNote, float]]:
    """Semantic search using vector similarity. Returns (note, distance) pairs."""
    import numpy as np

    rows = conn.execute(
        """
        SELECT notes.*, notes_vec.distance
        FROM notes_vec
        JOIN notes ON notes.rowid = notes_vec.note_rowid
        WHERE notes_vec.embedding MATCH ? AND k = ?
        ORDER BY distance
        """,
        (query_embedding.astype(np.float32), limit),
    ).fetchall()

    return [(_row_to_note(row), row["distance"]) for row in rows]
