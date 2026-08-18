"""URL validation — including the cases both original scripts got wrong."""

import pytest

from ytdl.core.errors import InvalidURLError
from ytdl.core.urls import extract_video_id, is_valid_youtube_url, normalize_url

VIDEO_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"http://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com/watch?v={VIDEO_ID}",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://music.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}?t=42",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}",
        f"https://www.youtube.com/v/{VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PLxyz&index=3",
        f"youtube.com/watch?v={VIDEO_ID}",  # scheme omitted
        f"  https://youtu.be/{VIDEO_ID}  ",  # surrounding whitespace
        VIDEO_ID,  # bare id
    ],
)
def test_accepts_every_common_share_form(url):
    assert extract_video_id(url) == VIDEO_ID


def test_normalize_collapses_all_forms_to_one_canonical_url():
    forms = [
        f"https://youtu.be/{VIDEO_ID}?t=90",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
    ]
    assert {normalize_url(f) for f in forms} == {
        f"https://www.youtube.com/watch?v={VIDEO_ID}"
    }


class TestRegressionsFromOriginalScripts:
    """Each of these was mishandled by the pre-merge validation."""

    def test_bare_youtube_com_is_accepted(self):
        # Original: `netloc == "www.youtube.com"` rejected this valid URL.
        assert is_valid_youtube_url(f"https://youtube.com/watch?v={VIDEO_ID}")

    def test_mobile_host_is_accepted(self):
        # Original rejected m.youtube.com — what phone sharing produces.
        assert is_valid_youtube_url(f"https://m.youtube.com/watch?v={VIDEO_ID}")

    def test_foreign_host_mentioning_youtu_be_is_rejected(self):
        # Original: `"youtu.be" in video_url` matched anywhere in the string,
        # so this hostile URL passed validation and was downloaded from.
        assert not is_valid_youtube_url("https://example.com/?ref=youtu.be")
        assert not is_valid_youtube_url("https://youtu.be.evil.com/watch?v=" + VIDEO_ID)

    def test_shorts_links_are_accepted(self):
        # Original handled neither /shorts/ nor /embed/.
        assert is_valid_youtube_url(f"https://www.youtube.com/shorts/{VIDEO_ID}")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url at all",
        "https://vimeo.com/123456789",
        "https://example.com/watch?v=" + VIDEO_ID,
        "ftp://www.youtube.com/watch?v=" + VIDEO_ID,
        "https://www.youtube.com/",                      # no video
        "https://www.youtube.com/@somechannel",          # channel, not video
        "https://www.youtube.com/playlist?list=PLxyz",   # playlist, not video
        "https://www.youtube.com/watch?v=tooshort",      # malformed id
        "https://www.youtube.com/watch?v=way_too_long_to_be_an_id",
    ],
)
def test_rejects_invalid_input(url):
    assert not is_valid_youtube_url(url)
    with pytest.raises(InvalidURLError):
        extract_video_id(url)


def test_error_carries_a_displayable_message():
    with pytest.raises(InvalidURLError) as excinfo:
        extract_video_id("https://vimeo.com/123")
    assert excinfo.value.user_message
    assert "vimeo.com" in excinfo.value.user_message
