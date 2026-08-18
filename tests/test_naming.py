"""Filename sanitization, including the data loss the original caused."""

import pytest

from ytdl.core.naming import (
    legacy_remove_non_alpha,
    sanitize_filename,
    unique_path,
)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Simple Title", "Simple-Title"),
        ("Top 10 Songs of 2024", "Top-10-Songs-of-2024"),
        ("AC/DC: Back in Black", "ACDC-Back-in-Black"),
        ('Quote "Test" Here', "Quote-Test-Here"),
        ("Multiple   Spaces", "Multiple-Spaces"),
        ("path\\with\\backslashes", "pathwithbackslashes"),
        ("Trailing dots...", "Trailing-dots"),
        ("  leading and trailing  ", "leading-and-trailing"),
    ],
)
def test_sanitize_produces_safe_names(title, expected):
    assert sanitize_filename(title) == expected


@pytest.mark.parametrize("title", ["", "   ", "???", "///", "..."])
def test_never_returns_empty(title):
    assert sanitize_filename(title) == "video"


@pytest.mark.parametrize("reserved", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9"])
def test_windows_reserved_device_names_are_defused(reserved):
    result = sanitize_filename(reserved)
    assert result != reserved
    assert result.split(".")[0].upper() not in {
        "CON", "PRN", "AUX", "NUL", "COM1", "LPT9"
    }


def test_no_control_characters_survive():
    assert sanitize_filename("bad\x00null\x1fchar") == "badnullchar"


def test_length_is_capped():
    assert len(sanitize_filename("A" * 500)) <= 120


def test_unicode_titles_are_preserved():
    # The original reduced these to empty strings, producing a file named ".mp4".
    assert sanitize_filename("日本語のタイトル") == "日本語のタイトル"
    assert sanitize_filename("Café Münster") == "Café-Münster"


class TestRegressionsFromOriginalScripts:
    def test_original_destroyed_digits(self):
        assert legacy_remove_non_alpha("Top 10 Songs of 2024") == "Top--Songs-of-"
        assert sanitize_filename("Top 10 Songs of 2024") == "Top-10-Songs-of-2024"

    def test_original_emptied_non_ascii_titles(self):
        assert legacy_remove_non_alpha("日本語") == ""
        assert sanitize_filename("日本語") == "日本語"

    def test_original_left_windows_reserved_names_intact(self):
        assert legacy_remove_non_alpha("CON") == "CON"
        assert sanitize_filename("CON") != "CON"


def test_unique_path_avoids_overwriting(tmp_path):
    first = unique_path(tmp_path, "clip", ".mp4")
    assert first.name == "clip.mp4"
    first.touch()

    second = unique_path(tmp_path, "clip", ".mp4")
    assert second.name == "clip-1.mp4"
    second.touch()

    assert unique_path(tmp_path, "clip", ".mp4").name == "clip-2.mp4"


def test_unique_path_accepts_suffix_without_dot(tmp_path):
    assert unique_path(tmp_path, "clip", "mp3").name == "clip.mp3"
