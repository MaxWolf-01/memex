"""SQLite database for note indexing with FTS5 and vector search."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sqlite_vec

from memex_md_mcp.parser import ParsedNote

DB_PATH = Path.home() / ".local/share/memex-md-mcp/memex.db"


@dataclass
class IndexedNote:
    """A note as stored in the database."""

    path: str  # relative path within vault
    vault: str  # vault identifier
    title: str
    aliases: list[str]
    tags: list[str]
    content: str
    mtime: float
    content_hash: str


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a database connection with optimal settings and sqlite-vec loaded."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
-- Note metadata and content
CREATE TABLE IF NOT EXISTS notes (
    path TEXT NOT NULL,
    vault TEXT NOT NULL,
    title TEXT NOT NULL,
    aliases TEXT NOT NULL,       -- JSON array
    tags TEXT NOT NULL,          -- JSON array
    content TEXT NOT NULL,
    mtime REAL NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (path, vault)
);

CREATE INDEX IF NOT EXISTS idx_notes_vault ON notes(vault);
CREATE INDEX IF NOT EXISTS idx_notes_mtime ON notes(mtime);

-- Full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path,
    vault,
    title,
    aliases,
    tags,
    content,
    content='notes',
    content_rowid='rowid'
);

-- Triggers to keep FTS in sync with notes table
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, path, vault, title, aliases, tags, content)
    VALUES (NEW.rowid, NEW.path, NEW.vault, NEW.title, NEW.aliases, NEW.tags, NEW.content);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, path, vault, title, aliases, tags, content)
    VALUES ('delete', OLD.rowid, OLD.path, OLD.vault, OLD.title, OLD.aliases, OLD.tags, OLD.content);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, path, vault, title, aliases, tags, content)
    VALUES ('delete', OLD.rowid, OLD.path, OLD.vault, OLD.title, OLD.aliases, OLD.tags, OLD.content);
    INSERT INTO notes_fts(rowid, path, vault, title, aliases, tags, content)
    VALUES (NEW.rowid, NEW.path, NEW.vault, NEW.title, NEW.aliases, NEW.tags, NEW.content);
END;

-- Wikilink graph for backlink queries
CREATE TABLE IF NOT EXISTS wikilinks (
    source_path TEXT NOT NULL,
    source_vault TEXT NOT NULL,
    target_raw TEXT NOT NULL,
    target_path TEXT,            -- resolved path, NULL if unresolved
    FOREIGN KEY (source_path, source_vault) REFERENCES notes(path, vault) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wikilinks_source ON wikilinks(source_path, source_vault);
CREATE INDEX IF NOT EXISTS idx_wikilinks_target ON wikilinks(target_path);
"""

EMBEDDING_DIM = 768

VEC_SCHEMA = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec USING vec0(
    note_rowid INTEGER PRIMARY KEY,
    embedding float[{EMBEDDING_DIM}] distance_metric=cosine
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize database schema."""
    conn.executescript(SCHEMA)
    conn.executescript(VEC_SCHEMA)
    conn.commit()


def upsert_note(
    conn: sqlite3.Connection,
    vault: str,
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
        INSERT INTO notes (path, vault, title, aliases, tags, content, mtime, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path, vault) DO UPDATE SET
            title = excluded.title,
            aliases = excluded.aliases,
            tags = excluded.tags,
            content = excluded.content,
            mtime = excluded.mtime,
            content_hash = excluded.content_hash
        """,
        (path, vault, note.title, aliases_json, tags_json, note.content, mtime, content_hash),
    )

    # Replace wikilinks (delete old, insert new)
    conn.execute("DELETE FROM wikilinks WHERE source_path = ? AND source_vault = ?", (path, vault))
    if note.wikilinks:
        conn.executemany(
            "INSERT INTO wikilinks (source_path, source_vault, target_raw, target_path) VALUES (?, ?, ?, NULL)",
            [(path, vault, target) for target in note.wikilinks],
        )

    conn.commit()


def delete_note(conn: sqlite3.Connection, vault: str, path: str) -> None:
    """Delete a note (wikilinks cascade automatically)."""
    conn.execute("DELETE FROM notes WHERE path = ? AND vault = ?", (path, vault))
    conn.commit()


def delete_vault(conn: sqlite3.Connection, vault: str) -> int:
    """Delete all notes for a vault. Returns count of deleted notes."""
    cursor = conn.execute("DELETE FROM notes WHERE vault = ?", (vault,))
    conn.commit()
    return cursor.rowcount


def get_note(conn: sqlite3.Connection, vault: str, path: str) -> IndexedNote | None:
    """Retrieve a single note by vault and path. Extension .md is optional."""
    row = conn.execute(
        "SELECT * FROM notes WHERE path = ? AND vault = ?",
        (path, vault),
    ).fetchone()
    if not row and not path.endswith(".md"):
        row = conn.execute(
            "SELECT * FROM notes WHERE path = ? AND vault = ?",
            (path + ".md", vault),
        ).fetchone()
    if not row:
        return None
    return IndexedNote(
        path=row["path"],
        vault=row["vault"],
        title=row["title"],
        aliases=json.loads(row["aliases"]),
        tags=json.loads(row["tags"]),
        content=row["content"],
        mtime=row["mtime"],
        content_hash=row["content_hash"],
    )


def get_indexed_mtimes(conn: sqlite3.Connection, vault: str) -> dict[str, float]:
    """Get mtime for all notes in a vault. For staleness checking."""
    rows = conn.execute("SELECT path, mtime FROM notes WHERE vault = ?", (vault,)).fetchall()
    return {row["path"]: row["mtime"] for row in rows}


def list_notes(conn: sqlite3.Connection, vault: str | None = None, limit: int | None = None) -> list[IndexedNote]:
    """List all notes, optionally filtered by vault."""
    if vault:
        query = "SELECT * FROM notes WHERE vault = ? ORDER BY path"
        params: tuple = (vault,)
    else:
        query = "SELECT * FROM notes ORDER BY vault, path"
        params = ()

    if limit:
        query += " LIMIT ?"
        params = (*params, limit)

    rows = conn.execute(query, params).fetchall()
    return [
        IndexedNote(
            path=row["path"],
            vault=row["vault"],
            title=row["title"],
            aliases=json.loads(row["aliases"]),
            tags=json.loads(row["tags"]),
            content=row["content"],
            mtime=row["mtime"],
            content_hash=row["content_hash"],
        )
        for row in rows
    ]


def search_fts(conn: sqlite3.Connection, query: str, vault: str | None = None, limit: int = 10) -> list[IndexedNote]:
    """Full-text search across notes.

    Args:
        query: FTS5 query string (supports AND, OR, NOT, phrase matching)
        vault: Optional vault filter
        limit: Maximum results to return
    """
    if vault:
        rows = conn.execute(
            """
            SELECT notes.* FROM notes_fts
            JOIN notes ON notes.rowid = notes_fts.rowid
            WHERE notes_fts MATCH ? AND notes.vault = ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, vault, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT notes.* FROM notes_fts
            JOIN notes ON notes.rowid = notes_fts.rowid
            WHERE notes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()

    return [
        IndexedNote(
            path=row["path"],
            vault=row["vault"],
            title=row["title"],
            aliases=json.loads(row["aliases"]),
            tags=json.loads(row["tags"]),
            content=row["content"],
            mtime=row["mtime"],
            content_hash=row["content_hash"],
        )
        for row in rows
    ]


def get_outlinks(conn: sqlite3.Connection, vault: str, path: str) -> list[tuple[str, list[str]]]:
    """Get all wikilink targets from a note (what this note links TO).

    Returns list of (target_raw, resolved_paths) tuples.
    resolved_paths is a list of matching note paths (can be empty if unresolved,
    or multiple if case-insensitive matching finds duplicates like "Foo" and "foo").
    Extension .md is optional in path.
    """
    rows = conn.execute(
        "SELECT target_raw FROM wikilinks WHERE source_vault = ? AND source_path = ?",
        (vault, path),
    ).fetchall()
    if not rows and not path.endswith(".md"):
        rows = conn.execute(
            "SELECT target_raw FROM wikilinks WHERE source_vault = ? AND source_path = ?",
            (vault, path + ".md"),
        ).fetchall()

    results = []
    for row in rows:
        target = row["target_raw"]
        resolved = resolve_wikilink(conn, vault, target)
        results.append((target, resolved))
    return results


def resolve_wikilink(conn: sqlite3.Connection, vault: str, target: str) -> list[str]:
    """Resolve a wikilink target to note path(s).

    Matches against note title (case-insensitive). Returns all matching paths
    in case of duplicates (e.g., "Attention.md" and "attention.md" both exist).
    """
    rows = conn.execute(
        "SELECT path FROM notes WHERE vault = ? AND LOWER(title) = LOWER(?)",
        (vault, target),
    ).fetchall()
    return [row["path"] for row in rows]


def get_backlinks(conn: sqlite3.Connection, vault: str, note_name: str) -> list[str]:
    """Find all notes that link TO the given note by title (case-insensitive).

    Args:
        vault: Vault to search in
        note_name: The note name as it appears in wikilinks, i.e. the filename without extension

    Note: Case-insensitive matching to match Obsidian's wikilink resolution behavior.
    For more precise link finding (including path-based links), use find_links_to_note().
    """
    rows = conn.execute(
        "SELECT DISTINCT source_path FROM wikilinks WHERE source_vault = ? AND LOWER(target_raw) = LOWER(?)",
        (vault, note_name),
    ).fetchall()
    return [row["source_path"] for row in rows]


def get_files_with_title(conn: sqlite3.Connection, vault: str, title: str) -> list[str]:
    """Find all files with the given title (case-insensitive).

    Returns list of paths. Used to detect ambiguous wikilinks.
    """
    rows = conn.execute(
        "SELECT path FROM notes WHERE vault = ? AND LOWER(title) = LOWER(?)",
        (vault, title),
    ).fetchall()
    return [row["path"] for row in rows]


def get_files_with_path(conn: sqlite3.Connection, vault: str, path_without_ext: str) -> list[str]:
    """Find all files with the given path (case-insensitive, without .md extension).

    Returns list of paths. Used to detect path-based link ambiguity on case-sensitive filesystems.
    """
    rows = conn.execute(
        "SELECT path FROM notes WHERE vault = ? AND LOWER(path) = LOWER(?)",
        (vault, path_without_ext + ".md"),
    ).fetchall()
    return [row["path"] for row in rows]


def find_links_to_note(
    conn: sqlite3.Connection, vault: str, note_path: str
) -> list[tuple[str, str, str]]:
    """Find all wikilinks that resolve to the given note.

    Returns list of (source_path, target_raw, link_type) tuples where link_type is:
    - "path": Link uses path (e.g., [[subdir/note]] for subdir/note.md)
    - "path_ambiguous": Path link but multiple files match (different case)
    - "title": Link uses title (e.g., [[note]] for note.md or subdir/note.md)
    - "title_ambiguous": Title link but multiple files share this title

    This handles:
    - Path-based links: [[subdir/note]] → matches subdir/note.md
    - Title-based links: [[note]] → matches any note.md in vault
    - Case variants: [[Note]], [[NOTE]] etc.
    - Same-path-different-case: [[subdir/Note]] vs [[subdir/note]]
    """
    title = Path(note_path).stem  # filename without .md
    path_without_ext = note_path.removesuffix(".md")

    results = []

    # Find path-based links (case-insensitive for cross-platform)
    path_links = conn.execute(
        "SELECT source_path, target_raw FROM wikilinks WHERE source_vault = ? AND LOWER(target_raw) = LOWER(?)",
        (vault, path_without_ext),
    ).fetchall()

    # Check if path is ambiguous (multiple files with same path, different case)
    files_with_path = get_files_with_path(conn, vault, path_without_ext)
    is_path_ambiguous = len(files_with_path) > 1

    for row in path_links:
        link_type = "path_ambiguous" if is_path_ambiguous else "path"
        results.append((row["source_path"], row["target_raw"], link_type))

    # Find title-based links (just the filename, no path)
    # But only if target_raw doesn't contain '/' (otherwise it's a path link)
    title_links = conn.execute(
        """SELECT source_path, target_raw FROM wikilinks
           WHERE source_vault = ? AND LOWER(target_raw) = LOWER(?) AND target_raw NOT LIKE '%/%'""",
        (vault, title),
    ).fetchall()

    # Check if title is ambiguous (multiple files with same title)
    files_with_title = get_files_with_title(conn, vault, title)
    is_title_ambiguous = len(files_with_title) > 1

    for row in title_links:
        link_type = "title_ambiguous" if is_title_ambiguous else "title"
        results.append((row["source_path"], row["target_raw"], link_type))

    return results


def upsert_embedding(conn: sqlite3.Connection, note_rowid: int, embedding: np.ndarray) -> None:
    """Insert or update embedding for a note."""
    conn.execute("DELETE FROM notes_vec WHERE note_rowid = ?", (note_rowid,))
    conn.execute(
        "INSERT INTO notes_vec (note_rowid, embedding) VALUES (?, ?)",
        (note_rowid, embedding.astype(np.float32)),
    )
    conn.commit()


def get_note_rowid(conn: sqlite3.Connection, vault: str, path: str) -> int | None:
    """Get the rowid for a note by vault and path. Extension .md is optional."""
    row = conn.execute("SELECT rowid FROM notes WHERE vault = ? AND path = ?", (vault, path)).fetchone()
    if not row and not path.endswith(".md"):
        row = conn.execute("SELECT rowid FROM notes WHERE vault = ? AND path = ?", (vault, path + ".md")).fetchone()
    return row[0] if row else None


def get_note_embedding(conn: sqlite3.Connection, vault: str, path: str) -> np.ndarray | None:
    """Get the embedding vector for a note."""
    rowid = get_note_rowid(conn, vault, path)
    if rowid is None:
        return None
    row = conn.execute("SELECT embedding FROM notes_vec WHERE note_rowid = ?", (rowid,)).fetchone()
    return np.frombuffer(row[0], dtype=np.float32) if row else None


def search_semantic(
    conn: sqlite3.Connection, query_embedding: np.ndarray, vault: str | None = None, limit: int = 10
) -> list[tuple[IndexedNote, float]]:
    """Semantic search using vector similarity. Returns (note, distance) pairs."""
    if vault:
        rows = conn.execute(
            """
            SELECT notes.*, notes_vec.distance
            FROM notes_vec
            JOIN notes ON notes.rowid = notes_vec.note_rowid
            WHERE notes_vec.embedding MATCH ? AND k = ? AND notes.vault = ?
            ORDER BY distance
            """,
            (query_embedding.astype(np.float32), limit, vault),
        ).fetchall()
    else:
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

    return [
        (
            IndexedNote(
                path=row["path"],
                vault=row["vault"],
                title=row["title"],
                aliases=json.loads(row["aliases"]),
                tags=json.loads(row["tags"]),
                content=row["content"],
                mtime=row["mtime"],
                content_hash=row["content_hash"],
            ),
            row["distance"],
        )
        for row in rows
    ]
