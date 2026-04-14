"""Tests for the read command (obsidian-cli eval integration)."""

import json
import subprocess
from unittest.mock import patch

import pytest

from memex_md.cli import do_read


def _mock_eval(stdout: str, returncode: int = 0, stderr: str = ""):
    """Create a mock for subprocess.run that simulates obsidian-cli eval output."""
    return subprocess.CompletedProcess(
        args=["obsidian-cli", "eval", "code=..."],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _eval_string_output(text: str) -> str:
    """Format text as obsidian eval would return a string result."""
    return f"=> {json.dumps(text)}\n"


@pytest.fixture(autouse=True)
def _obsidian_cli_available():
    """Pretend obsidian-cli is installed for all tests in this module."""
    with patch("memex_md.cli.shutil.which", return_value="/usr/bin/obsidian-cli"):
        yield


# ── obsidian-cli availability ───────────────────────────────────────────────


def test_read_missing_obsidian_cli():
    with patch("memex_md.cli.shutil.which", return_value=None):
        result = do_read(ref="anything")
    assert "error" in result
    assert "obsidian-cli not found" in result["error"]


# ── ref parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ref, expected_note, expected_subpath",
    [
        ("my note", "my note", None),
        ("note#heading", "note", "heading"),
        ("note#^block-id", "note", "^block-id"),
        ("note#Heading With Spaces", "note", "Heading With Spaces"),
        ("note with spaces#^abc123", "note with spaces", "^abc123"),
        # First # splits, rest is subpath
        ("note#heading#with#hashes", "note", "heading#with#hashes"),
    ],
)
def test_ref_parsing(ref, expected_note, expected_subpath):
    """Verify the ref is split into note + subpath correctly in the JS template."""
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output("content"))
        do_read(ref=ref)

        call_args = mock_run.call_args[0][0]
        code_arg = next(a for a in call_args if a.startswith("code="))
        assert json.dumps(expected_note) in code_arg
        if expected_subpath:
            assert json.dumps(expected_subpath) in code_arg
        else:
            assert "%%SUBPATH%%" not in code_arg


# ── output parsing ──────────────────────────────────────────────────────────


def test_plain_string_result():
    """obsidian eval returns JSON-encoded strings: => "content here"."""
    content = "Line 1\nLine 2\n> [!note] A callout"
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output(content))
        result = do_read(ref="test")
    assert result == {"content": content}


def test_string_with_special_chars():
    """Strings with quotes, backslashes, unicode are JSON-decoded correctly."""
    content = 'She said "hello" and\\used $\\LaTeX$ → émojis 🎉'
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output(content))
        result = do_read(ref="test")
    assert result == {"content": content}


def test_multiline_callout_content():
    """Realistic multi-line callout content is preserved."""
    content = (
        "> [!definition] Gradient descent\n"
        "An optimization algorithm that iteratively adjusts parameters.\n"
        "Used in training neural networks."
    )
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output(content))
        result = do_read(ref="optimization#^abc123")
    assert result == {"content": content}


def test_output_without_arrow_prefix():
    """Handle output that doesn't start with '=> ' (future-proofing)."""
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval("raw output without prefix\n")
        result = do_read(ref="test")
    assert result == {"content": "raw output without prefix\n"}


def test_invalid_json_after_quote_raises():
    """If output starts with '"' but isn't valid JSON, that's a broken invariant."""
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval('=> "unterminated string\n')
        with pytest.raises(json.JSONDecodeError):
            do_read(ref="test")


# ── error handling ──────────────────────────────────────────────────────────


def test_note_not_found():
    """obsidian eval returns 'Error: not found: ...' for missing notes."""
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output("Error: not found: nonexistent"))
        result = do_read(ref="nonexistent")
    assert "error" in result
    assert "not found" in result["error"]


def test_block_not_found():
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output("Error: block not found: ^xyz"))
        result = do_read(ref="note#^xyz")
    assert "error" in result
    assert "block not found" in result["error"]


def test_obsidian_not_running():
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval("", returncode=1, stderr="Obsidian is not running")
        result = do_read(ref="test")
    assert "error" in result
    assert "not running" in result["error"].lower()


def test_cli_not_enabled():
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval("", returncode=1, stderr="Command line interface is not enabled")
        result = do_read(ref="test")
    assert "error" in result
    assert "not enabled" in result["error"].lower()


def test_generic_eval_failure():
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval("", returncode=1, stderr="Something unexpected")
        result = do_read(ref="test")
    assert "error" in result
    assert "Something unexpected" in result["error"]


# ── JS template substitution ───────────────────────────────────────────────


def test_special_chars_in_note_name():
    """Note names with quotes/backslashes are JSON-escaped in the JS template."""
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output("ok"))
        do_read(ref='note "with" quotes')

        code_arg = next(a for a in mock_run.call_args[0][0] if a.startswith("code="))
        assert r"note \"with\" quotes" in code_arg


def test_strip_frontmatter_flag():
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output("ok"))

        do_read(ref="test", strip_frontmatter=False)
        code_false = next(a for a in mock_run.call_args[0][0] if a.startswith("code="))
        assert "false" in code_false.split("stripFMTop")[1][:20]

        do_read(ref="test", strip_frontmatter=True)
        code_true = next(a for a in mock_run.call_args[0][0] if a.startswith("code="))
        assert "true" in code_true.split("stripFMTop")[1][:20]


def test_max_depth_substitution():
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output("ok"))
        do_read(ref="test", max_depth=3)

        code_arg = next(a for a in mock_run.call_args[0][0] if a.startswith("code="))
        assert "const maxDepth = 3;" in code_arg


# ── realistic embed resolution outputs ──────────────────────────────────────


def test_inline_block_ref_output():
    """Inline ^id content is returned with the id suffix stripped."""
    content = "> [!definition] A function maps each input to exactly one output."
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output(content))
        result = do_read(ref="function#^def01")
    assert result["content"] == content


def test_standalone_block_ref_output():
    """Standalone ^id (own line, gap above) returns the preceding paragraph."""
    content = "> [!quote] The only way to do great work is to love what you do."
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output(content))
        result = do_read(ref="motivation#^q42")
    assert result["content"] == content


def test_full_note_with_resolved_embeds():
    """A note with multiple embeds returns all content inlined."""
    content = (
        "> [!quote] First principle.\n"
        "\n"
        "^ref1\n"
        "\n"
        "Connecting paragraph about the topic.\n"
        "\n"
        "> [!quote] Second principle from another note."
    )
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output(content))
        result = do_read(ref="principles")
    assert "First principle" in result["content"]
    assert "Second principle" in result["content"]


def test_nested_callout_block():
    """Multi-line callout with nested blockquotes is returned as a single block."""
    content = (
        "> [!note] Key observation\n"
        ">>When optimizing for a single metric, other factors are neglected.\n"
        ">\n"
        '"This is fine" is rarely true.'
    )
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output(content))
        result = do_read(ref="tradeoffs#^obs1")
    assert "Key observation" in result["content"]
    assert "This is fine" in result["content"]


def test_heading_extraction_output():
    """Heading ref returns only the content under that heading."""
    content = "Some items:\n- Item A\n- Item B"
    with patch("memex_md.cli.subprocess.run") as mock_run:
        mock_run.return_value = _mock_eval(_eval_string_output(content))
        result = do_read(ref="overview#References")
    assert result["content"] == content
