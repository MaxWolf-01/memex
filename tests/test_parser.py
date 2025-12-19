"""Tests for markdown parser."""

import tempfile

import pytest

from memex_md_mcp.parser import (
    TAG_PATTERN,
    WIKILINK_PATTERN,
    parse_note,
    strip_code,
)


class TestWikilinkPattern:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("[[note]]", ["note"]),
            ("[[note|display]]", ["note"]),
            ("[[note#heading]]", ["note"]),
            ("[[note#heading|display]]", ["note"]),
            ("[[note#^block-id]]", ["note"]),
            ("[[note#^block-id|alias]]", ["note"]),
            ("[[folder/note]]", ["folder/note"]),
            ("[[note with spaces]]", ["note with spaces"]),
            ("text [[a]] and [[b]] more", ["a", "b"]),
            ("no links here", []),
        ],
    )
    def test_wikilink_extraction(self, input_text: str, expected: list[str]):
        assert WIKILINK_PATTERN.findall(input_text) == expected


class TestTagPattern:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("#tag", ["tag"]),
            ("#tag/subtag", ["tag/subtag"]),
            ("text #tag more", ["tag"]),
            ("#one #two #three", ["one", "two", "three"]),
            ("issue#123", []),  # no space before #
            ("http://example.com#fragment", []),  # URL fragment
            ("#tag-with-dash", ["tag-with-dash"]),
            ("#tag_with_underscore", ["tag_with_underscore"]),
            ("no tags here", []),
        ],
    )
    def test_tag_extraction(self, input_text: str, expected: list[str]):
        assert TAG_PATTERN.findall(input_text) == expected


class TestStripCode:
    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("normal text", "normal text"),
            ("`inline code`", ""),
            ("before `code` after", "before  after"),
            ("```python\ncode\n```", ""),
            ("before\n```\ncode\n```\nafter", "before\n\nafter"),
            ("```\n#not-a-tag\n```", ""),
        ],
    )
    def test_strip_code(self, input_text: str, expected: str):
        assert strip_code(input_text) == expected


class TestParseNote:
    def test_basic_note(self):
        content = "# Title\n\nSome content with #tag and [[link]]."
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()
            result = parse_note(f.name, "test-note.md")

        assert result.title == "test-note"
        assert result.tags == ["tag"]
        assert result.wikilinks == ["link"]
        assert "#tag" in result.content

    def test_frontmatter_aliases(self):
        content = """---
aliases:
  - alias1
  - alias2
---
Content here."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()
            result = parse_note(f.name, "note.md")

        assert result.aliases == ["alias1", "alias2"]

    def test_frontmatter_tags(self):
        content = """---
tags:
  - fm-tag1
  - fm-tag2
---
Content with #inline-tag."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()
            result = parse_note(f.name, "note.md")

        assert result.tags == ["fm-tag1", "fm-tag2", "inline-tag"]

    def test_string_alias(self):
        content = """---
aliases: single-alias
---
Content."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()
            result = parse_note(f.name, "note.md")

        assert result.aliases == ["single-alias"]

    def test_code_block_ignored(self):
        content = """# Note

```python
# this is a comment, not a tag
link = "[[not-a-link]]"
```

Real #tag and [[real-link]]."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()
            result = parse_note(f.name, "note.md")

        assert result.tags == ["tag"]
        assert result.wikilinks == ["real-link"]

    def test_deduplication(self):
        content = "#tag #tag [[link]] [[link]]"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            f.flush()
            result = parse_note(f.name, "note.md")

        assert result.tags == ["tag"]
        assert result.wikilinks == ["link"]
