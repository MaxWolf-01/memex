"""Markdown parsing: extract frontmatter, tags, wikilinks."""

import re
from dataclasses import dataclass

import frontmatter


@dataclass
class ParsedNote:
    title: str  # filename without .md
    aliases: list[str]  # from YAML frontmatter
    tags: list[str]  # #tag from content + frontmatter
    wikilinks: list[str]  # [[target]] links
    content: str  # full content (minus frontmatter)


# Wikilinks: [[target]], [[target|display]], [[target#heading]], [[target#heading|display]]
# Capture group 1 = target (before | or # if present)
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")

# Tags: #tag, #tag/subtag - must not be preceded by non-whitespace
# Excludes things like "issue#123" or URLs with fragments
TAG_PATTERN = re.compile(r"(?<!\S)#([\w/-]+)")

# Code blocks to strip before extracting tags/links
FENCED_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
INLINE_CODE = re.compile(r"`[^`]+`")


def _normalize_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def strip_code(content: str) -> str:
    """Remove code blocks and inline code to avoid false positives."""
    content = FENCED_CODE_BLOCK.sub("", content)
    content = INLINE_CODE.sub("", content)
    return content


def parse_note(filepath: str, filename: str) -> ParsedNote:
    """Parse a markdown file and extract metadata.

    Args:
        filepath: Absolute path to the .md file
        filename: Just the filename (used for title)
    """
    with open(filepath, encoding="utf-8") as f:
        post = frontmatter.load(f)

    # Title = filename without extension
    title = filename.removesuffix(".md")

    # Aliases from frontmatter (can be string or list)
    aliases = _normalize_list(post.metadata.get("aliases"))

    # Tags from frontmatter
    fm_tags = _normalize_list(post.metadata.get("tags"))

    # Content (without frontmatter)
    content = post.content

    # Extract tags and wikilinks from content (after stripping code)
    stripped = strip_code(content)
    content_tags = TAG_PATTERN.findall(stripped)
    wikilinks = WIKILINK_PATTERN.findall(stripped)

    # Combine frontmatter + content tags, dedupe while preserving order
    all_tags = fm_tags + content_tags
    seen = set()
    tags = []
    for tag in all_tags:
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)

    # Dedupe wikilinks too
    seen_links = set()
    unique_links = []
    for link in wikilinks:
        if link not in seen_links:
            seen_links.add(link)
            unique_links.append(link)

    return ParsedNote(
        title=title,
        aliases=aliases,
        tags=tags,
        wikilinks=unique_links,
        content=content,
    )
