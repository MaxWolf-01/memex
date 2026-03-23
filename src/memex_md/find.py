"""Fuzzy find notes by title, alias, and path."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

# Tier bonuses added on top of the fuzzy score (0-100) to separate match quality.
EXACT_BONUS = 200
SUBSTRING_BONUS = 100

# Minimum fuzzy score (0-100) for a match to count at all.
MIN_SCORE = 45


@dataclass(slots=True)
class FindResult:
    path: str
    title: str
    aliases: list[str]
    score: float


def _score_part(part: str, title_lower: str, aliases_lower: list[str], path_lower: str) -> float:
    """Score a single query part against one note. Returns best score across all fields."""
    best = 0.0

    # --- title ---
    if part == title_lower:
        return EXACT_BONUS + 100  # perfect, early exit
    if part in title_lower:
        best = max(best, SUBSTRING_BONUS + fuzz.ratio(part, title_lower))
    else:
        s = fuzz.WRatio(part, title_lower)
        if s >= MIN_SCORE:
            best = max(best, s)

    # --- aliases ---
    for alias in aliases_lower:
        if part == alias:
            return EXACT_BONUS + 100
        if part in alias:
            best = max(best, SUBSTRING_BONUS + fuzz.ratio(part, alias))
        else:
            s = fuzz.WRatio(part, alias)
            if s >= MIN_SCORE:
                best = max(best, s)

    # --- path (slightly discounted — less specific than title/alias) ---
    if part in path_lower:
        best = max(best, SUBSTRING_BONUS + fuzz.ratio(part, path_lower) * 0.8)
    else:
        s = fuzz.WRatio(part, path_lower) * 0.8
        if s >= MIN_SCORE:
            best = max(best, s)

    return best


def find_notes(
    notes: list[tuple[str, str, list[str]]],
    query: str,
    limit: int = 10,
) -> list[FindResult]:
    """Find notes matching query by title, alias, and path.

    Args:
        notes: List of (path, title, aliases) tuples from the database.
        query: User query string, split on whitespace into parts.
        limit: Maximum number of results to return.

    Returns:
        Sorted list of FindResult, best match first.
    """
    parts = query.lower().split()
    if not parts:
        return []

    results: list[FindResult] = []

    for path, title, aliases in notes:
        title_lower = title.lower()
        aliases_lower = [a.lower() for a in aliases]
        path_lower = path.lower()

        total = 0.0
        for part in parts:
            s = _score_part(part, title_lower, aliases_lower, path_lower)
            if s > 0:
                total += s

        if total > 0:
            results.append(FindResult(path=path, title=title, aliases=aliases, score=total))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
