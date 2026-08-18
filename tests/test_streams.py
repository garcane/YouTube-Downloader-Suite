"""Option building, exercised against a stubbed pytubefix.

Live YouTube access is not available in CI (and is rate-limited besides), so the
pytubefix surface the core actually uses is reproduced here. This covers the
logic that decides *what the user is offered*, which is the part of streams.py
worth protecting against regressions.
"""

import pytest

from ytdl.core import streams
from ytdl.core.errors import StreamNotFoundError, VideoUnavailableError


class FakeStream:
    def __init__(self, itag, *, resolution=None, abr=None, progressive=False,
                 only_video=False, only_audio=False, mime_type="video/mp4", fps=30,
                 subtype="mp4", codecs=("avc1.4d400c",)):
        self.itag = itag
        self.resolution = resolution
        self.abr = abr
        self.is_progressive = progressive
        self.is_adaptive = not progressive
        self.only_video = only_video
        self.only_audio = only_audio
        self.mime_type = mime_type
        self.fps = fps
        self.subtype = subtype
        self.codecs = list(codecs)
        self.filesize_approx = 1_000_000
        self.filesize = 1_000_000


class FakeQuery:
    """Reproduces the chained filter/order_by/desc/first API the core relies on."""

    def __init__(self, items):
        self._items = list(items)

    def filter(self, progressive=None, adaptive=None, only_video=None, only_audio=None, **kw):
        items = self._items
        if progressive is not None:
            items = [s for s in items if s.is_progressive == progressive]
        if adaptive is not None:
            items = [s for s in items if s.is_adaptive == adaptive]
        if only_video:
            items = [s for s in items if s.only_video]
        if only_audio:
            items = [s for s in items if s.only_audio]
        return FakeQuery(items)

    def order_by(self, attr):
        def key(s):
            value = getattr(s, attr, None) or ""
            digits = "".join(c for c in str(value) if c.isdigit())
            return int(digits) if digits else 0
        return FakeQuery(sorted(self._items, key=key))

    def desc(self):
        return FakeQuery(list(reversed(self._items)))

    def first(self):
        return self._items[0] if self._items else None

    def get_by_itag(self, itag):
        return next((s for s in self._items if s.itag == itag), None)

    def __iter__(self):
        return iter(self._items)


class FakeYouTube:
    title = "Test Video 2024"
    author = "Test Channel"
    length = 213
    thumbnail_url = "https://example.com/thumb.jpg"

    def __init__(self, url, **kwargs):
        self.url = url
        self.streams = FakeQuery([
            FakeStream(18, resolution="360p", progressive=True),
            FakeStream(22, resolution="720p", progressive=True),
            FakeStream(137, resolution="1080p", only_video=True),
            FakeStream(271, resolution="1440p", only_video=True),
            FakeStream(313, resolution="2160p", only_video=True),
            FakeStream(160, resolution="144p", only_video=True),   # must be filtered out
            FakeStream(140, abr="128kbps", only_audio=True, mime_type="audio/mp4"),
        ])


@pytest.fixture
def info(monkeypatch):
    monkeypatch.setattr("pytubefix.YouTube", FakeYouTube)
    return streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


class TestVideoOptions:
    def test_metadata_is_carried_through(self, info):
        assert info.title == "Test Video 2024"
        assert info.author == "Test Channel"
        assert info.length_seconds == 213
        assert info.video_id == "dQw4w9WgXcQ"

    def test_144p_is_excluded(self, info):
        # Both original scripts excluded 144p; that behaviour is preserved.
        assert "144p" not in [o.label for o in info.video_options]

    def test_resolutions_are_offered_high_to_low(self, info):
        labels = [o.label for o in info.video_options]
        assert labels == ["2160p", "1440p", "1080p", "720p", "360p"]

    def test_no_duplicate_resolutions(self, info):
        labels = [o.label for o in info.video_options]
        assert len(labels) == len(set(labels))

    def test_progressive_streams_need_no_ffmpeg(self, info):
        for option in info.video_options:
            if option.label in ("360p", "720p"):
                assert option.progressive
                assert not option.requires_ffmpeg

    def test_adaptive_streams_are_flagged_for_merging(self, info):
        for option in info.video_options:
            if option.label in ("1080p", "1440p", "2160p"):
                assert option.needs_merge
                assert option.requires_ffmpeg

    def test_highest_quality_is_the_recommended_one(self, info):
        recommended = [o for o in info.video_options if o.recommended]
        assert len(recommended) == 1
        assert recommended[0].label == "2160p"


class TestAudioOptions:
    def test_m4a_passthrough_is_offered_without_ffmpeg(self, info):
        m4a = next(o for o in info.audio_options if "M4A" in o.label)
        assert not m4a.requires_ffmpeg
        assert m4a.itag == 140

    def test_full_mp3_bitrate_ladder_is_offered(self, info):
        labels = [o.label for o in info.audio_options if o.label.startswith("MP3")]
        assert labels == [f"MP3 {b}" for b, _ in streams.MP3_BITRATES]

    def test_mp3_requires_transcoding(self, info):
        for option in info.audio_options:
            if option.label.startswith("MP3"):
                assert option.needs_transcode and option.requires_ffmpeg


class TestLookup:
    def test_find_is_case_insensitive(self, info):
        assert info.find("1080P").label == "1080p"
        assert info.find("mp3 320k").label == "MP3 320k"

    def test_unknown_option_lists_the_alternatives(self, info):
        with pytest.raises(StreamNotFoundError) as excinfo:
            info.find("4320p")
        assert "1080p" in (excinfo.value.hint or "")


class TestSerialisation:
    def test_info_is_json_ready(self, info):
        import json
        json.dumps(info.as_dict())  # must not raise

    def test_no_streams_is_an_error(self, monkeypatch):
        class Empty(FakeYouTube):
            def __init__(self, url, **kwargs):
                self.streams = FakeQuery([])

        monkeypatch.setattr("pytubefix.YouTube", Empty)
        with pytest.raises(StreamNotFoundError):
            streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


class TestErrorTranslation:
    @pytest.mark.parametrize(
        "exc_name,expected_fragment",
        [
            ("VideoPrivate", "private"),
            ("MembersOnly", "members"),
            ("VideoRegionBlocked", "region"),
            ("LiveStreamError", "livestream"),
            ("AgeRestrictedError", "age-restricted"),
        ],
    )
    def test_pytubefix_errors_become_readable(self, monkeypatch, exc_name, expected_fragment):
        from pytubefix import exceptions as pte

        exc_class = getattr(pte, exc_name)

        class Failing(FakeYouTube):
            def __init__(self, url, **kwargs):
                try:
                    raise exc_class("x")
                except TypeError:  # some take no arguments
                    raise exc_class()

        monkeypatch.setattr("pytubefix.YouTube", Failing)
        with pytest.raises(VideoUnavailableError) as excinfo:
            streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert expected_fragment in excinfo.value.user_message.lower()

    def test_bot_detection_suggests_updating_pytubefix(self, monkeypatch):
        from pytubefix import exceptions as pte

        class Failing(FakeYouTube):
            def __init__(self, url, **kwargs):
                raise pte.BotDetection(video_id="x")

        monkeypatch.setattr("pytubefix.YouTube", Failing)
        with pytest.raises(VideoUnavailableError) as excinfo:
            streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert "pytubefix" in (excinfo.value.hint or "")


class TestClientFallback:
    """YouTube blocks pytubefix clients selectively, so the core tries several."""

    @pytest.fixture(autouse=True)
    def reset_remembered_client(self):
        original = streams._working_client
        streams._working_client = None
        yield
        streams._working_client = original

    def test_default_order_leads_with_a_progressive_capable_client(self):
        # MWEB is the only client still exposing progressive streams, which
        # download without needing ffmpeg at all.
        assert streams.DEFAULT_CLIENT_ORDER[0] == "MWEB"

    def test_environment_variable_overrides_the_order(self, monkeypatch):
        monkeypatch.setenv(streams.ENV_CLIENT, "web, tv ,IOS")
        assert streams._client_candidates() == ["WEB", "TV", "IOS"]

    def test_a_working_client_is_tried_first_next_time(self):
        streams._working_client = "WEB_SAFARI"
        assert streams._client_candidates()[0] == "WEB_SAFARI"

    def test_falls_through_blocked_clients_to_a_working_one(self, monkeypatch):
        from pytubefix import exceptions as pte

        attempted = []

        def factory(url, client=None, **kwargs):
            attempted.append(client)
            if client in ("MWEB", "WEB"):
                raise pte.BotDetection(video_id="x")
            return FakeYouTube(url)

        monkeypatch.setattr("pytubefix.YouTube", factory)
        info = streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        assert attempted[:3] == ["MWEB", "WEB", "WEB_SAFARI"]
        assert info.title == "Test Video 2024"
        assert streams._working_client == "WEB_SAFARI"

    def test_definitive_failures_do_not_waste_time_on_other_clients(self, monkeypatch):
        from pytubefix import exceptions as pte

        attempted = []

        def factory(url, client=None, **kwargs):
            attempted.append(client)
            raise pte.VideoPrivate(video_id="x")

        monkeypatch.setattr("pytubefix.YouTube", factory)
        with pytest.raises(VideoUnavailableError, match="private"):
            streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        assert len(attempted) == 1  # no point retrying a private video

    def test_reports_failure_when_every_client_is_blocked(self, monkeypatch):
        from pytubefix import exceptions as pte

        def factory(url, client=None, **kwargs):
            raise pte.BotDetection(video_id="x")

        monkeypatch.setattr("pytubefix.YouTube", factory)
        with pytest.raises(VideoUnavailableError):
            streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_http_errors_are_treated_as_client_specific(self):
        import urllib.error

        exc = urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
        assert streams._is_client_specific_failure(exc)
        assert not streams._is_definitive_failure(exc)

    def test_private_video_is_definitive_not_client_specific(self):
        from pytubefix import exceptions as pte

        exc = pte.VideoPrivate(video_id="x")
        assert streams._is_definitive_failure(exc)


class TestCodecSelection:
    """Merging is a stream copy, so the codec chosen here is the codec delivered."""

    def test_h264_is_preferred_over_av1_and_vp9_at_the_same_resolution(self, monkeypatch):
        class MultiCodec(FakeYouTube):
            def __init__(self, url, **kwargs):
                self.url = url
                self.streams = FakeQuery([
                    FakeStream(242, resolution="240p", only_video=True,
                               subtype="webm", codecs=("vp9",)),
                    FakeStream(395, resolution="240p", only_video=True,
                               subtype="mp4", codecs=("av01.0.00M.08",)),
                    FakeStream(133, resolution="240p", only_video=True,
                               subtype="mp4", codecs=("avc1.4d400c",)),
                    FakeStream(140, abr="128kbps", only_audio=True,
                               subtype="mp4", codecs=("mp4a.40.2",)),
                ])

        monkeypatch.setattr("pytubefix.YouTube", MultiCodec)
        info = streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        option = info.find("240p")
        assert option.itag == 133          # H.264, not AV1 or VP9
        assert option.codec == "avc1"
        assert option.subtype == "mp4"

    def test_av1_wins_when_h264_is_not_offered(self, monkeypatch):
        class NoH264(FakeYouTube):
            def __init__(self, url, **kwargs):
                self.url = url
                self.streams = FakeQuery([
                    FakeStream(242, resolution="240p", only_video=True,
                               subtype="webm", codecs=("vp9",)),
                    FakeStream(395, resolution="240p", only_video=True,
                               subtype="mp4", codecs=("av01.0.00M.08",)),
                    FakeStream(140, abr="128kbps", only_audio=True,
                               subtype="mp4", codecs=("mp4a.40.2",)),
                ])

        monkeypatch.setattr("pytubefix.YouTube", NoH264)
        info = streams.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert info.find("240p").itag == 395   # MP4/AV1 beats WebM/VP9

    def test_codec_metadata_reaches_the_interfaces(self, info):
        for option in info.video_options:
            assert option.subtype in ("mp4", "webm")
        assert "codec" in info.options[0].as_dict()
