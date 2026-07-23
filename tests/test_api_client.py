"""Tests for resilient Bilibili API response parsing."""

import pytest

from bilibili_downloader.api.client import (
    FNVAL_4K,
    FNVAL_8K,
    FNVAL_AV1,
    FNVAL_DASH,
    FNVAL_DOLBY_AUDIO,
    FNVAL_DOLBY_VIDEO,
    FNVAL_HDR,
    BilibiliAPIClient,
    _build_fnval,
    _parse_playurl,
)
from bilibili_downloader.core.models import VideoQuality


def test_parse_playurl_supports_camel_case_and_premium_audio():
    parsed = _parse_playurl({
        "dash": {
            "video": [{
                "id": 80,
                "baseUrl": "https://cdn/video",
                "backupUrl": ["https://backup/video"],
                "mimeType": "video/mp4",
                "codecid": 12,
            }],
            "audio": None,
            "dolby": {
                "audio": [{
                    "id": 30250,
                    "baseUrl": "https://cdn/dolby",
                    "mimeType": "audio/mp4",
                }],
            },
            "flac": {
                "audio": {
                    "id": 30251,
                    "base_url": "https://cdn/flac",
                },
            },
        },
    })

    assert parsed["video_streams"][0].base_url == "https://cdn/video"
    assert parsed["video_streams"][0].backup_url == ["https://backup/video"]
    assert [stream.id for stream in parsed["audio_streams"]] == [30250, 30251]


def test_parse_playurl_accepts_missing_dash():
    parsed = _parse_playurl({"dash": None})

    assert parsed["video_streams"] == []
    assert parsed["audio_streams"] == []


def test_parse_playurl_ignores_malformed_stream_entries():
    parsed = _parse_playurl({
        "dash": {"video": [None], "audio": [None], "dolby": None}
    })

    assert parsed["video_streams"] == []
    assert parsed["audio_streams"] == []


@pytest.mark.parametrize(
    ("quality", "codec", "expected"),
    [
        (VideoQuality.Q1080P, 7, FNVAL_DASH),
        (VideoQuality.Q4K, 12, FNVAL_DASH | FNVAL_4K),
        (VideoQuality.QHDR, 12, FNVAL_DASH | FNVAL_HDR),
        (
            VideoQuality.Q_DOLBY,
            12,
            FNVAL_DASH | FNVAL_DOLBY_AUDIO | FNVAL_DOLBY_VIDEO,
        ),
        (VideoQuality.Q8K, 13, FNVAL_DASH | FNVAL_8K | FNVAL_AV1),
    ],
)
def test_build_fnval_requests_only_needed_capabilities(quality, codec, expected):
    assert _build_fnval(quality, preferred_codec=codec) == expected


def test_build_fnval_discovery_requests_all_capabilities():
    expected = (
        FNVAL_DASH | FNVAL_HDR | FNVAL_4K | FNVAL_DOLBY_AUDIO
        | FNVAL_DOLBY_VIDEO | FNVAL_8K | FNVAL_AV1
    )
    assert _build_fnval(VideoQuality.Q8K, discover_all=True) == expected


def test_parse_playurl_marks_hdr_quality_not_dolby_as_hdr():
    hdr = _parse_playurl({"dash": {"video": [{"id": 125}]}})
    dolby = _parse_playurl({"dash": {"video": [{"id": 126}]}})

    assert hdr["has_hdr"] is True
    assert dolby["has_hdr"] is False


def test_raw_wbi_retry_signs_a_fresh_parameter_copy(monkeypatch):
    client = BilibiliAPIClient(
        api_interval_range=(0, 0), risk_retry_delays=(0,)
    )
    signed_inputs = []
    responses = iter([
        {"code": -352, "message": "risk"},
        {"code": 0, "data": {"ok": True}},
    ])

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return next(responses)

    def sign(params):
        signed_inputs.append(dict(params))
        params["w_rid"] = f"signature-{len(signed_inputs)}"
        return params

    monkeypatch.setattr(client, "_sign_params", sign)
    monkeypatch.setattr(client._client, "get", lambda *_args, **_kwargs: Response())

    result = client._get_raw("/test", {"mid": 42}, signed=True)

    assert result["data"]["ok"] is True
    assert signed_inputs == [{"mid": 42}, {"mid": 42}]


def test_http_412_retries_with_a_fresh_wbi_signature(monkeypatch):
    import httpx

    client = BilibiliAPIClient(
        api_interval_range=(0, 0), risk_retry_delays=(0,)
    )
    signed_inputs = []
    request = httpx.Request("GET", "https://api.bilibili.com/test")
    responses = iter([
        httpx.Response(412, request=request),
        httpx.Response(
            200,
            json={"code": 0, "data": {"bvid": "BV0000000001"}},
            request=request,
        ),
    ])

    def sign(params):
        signed_inputs.append(dict(params))
        return {**params, "w_rid": f"signature-{len(signed_inputs)}"}

    monkeypatch.setattr(client, "_sign_params", sign)
    monkeypatch.setattr(client._client, "get", lambda *_args, **_kwargs: next(responses))

    info = client.get_video_info("BV0000000001")

    assert info.bvid == "BV0000000001"
    assert signed_inputs == [
        {"bvid": "BV0000000001"},
        {"bvid": "BV0000000001"},
    ]


def test_api_requests_are_serialized_across_threads(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    client = BilibiliAPIClient(api_interval_range=(0, 0))
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def get(*_args, **_kwargs):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        with state_lock:
            active -= 1
        return httpx.Response(
            200,
            json={"code": 0, "data": {}},
            request=httpx.Request("GET", "https://api.bilibili.com/test"),
        )

    monkeypatch.setattr(client._client, "get", get)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: client._request("/test"), range(4)))

    assert max_active == 1


def test_creator_video_page_includes_space_fingerprint(monkeypatch):
    client = BilibiliAPIClient(api_interval_range=(0, 0))
    captured = {}
    monkeypatch.setattr(client, "_ensure_creator_session", lambda _mid: None)

    def get_raw(endpoint, params, **kwargs):
        captured.update({"endpoint": endpoint, "params": params, **kwargs})
        return {"code": 0, "data": {}}

    monkeypatch.setattr(client, "_get_raw", get_raw)
    client.get_creator_video_page(42, 3)

    assert captured["params"]["mid"] == 42
    assert captured["params"]["pn"] == 3
    assert captured["params"]["dm_img_list"] == "[]"
    assert "dm_img_inter" in captured["params"]
    assert captured["signed"] is True
    assert captured["headers"]["Referer"].endswith("/42/video")


def test_creator_medialist_page_uses_cursor_without_wbi(monkeypatch):
    client = BilibiliAPIClient(api_interval_range=(0, 0))
    captured = {}

    def get_raw(endpoint, params, **kwargs):
        captured.update({"endpoint": endpoint, "params": params, **kwargs})
        return {"code": 0, "data": {}}

    monkeypatch.setattr(client, "_get_raw", get_raw)
    client.get_creator_medialist_page(42, cursor=987, page_size=20)

    assert captured["endpoint"].endswith("/x/v2/medialist/resource/list")
    assert captured["params"]["biz_id"] == 42
    assert captured["params"]["oid"] == 987
    assert captured["params"]["ps"] == 20
    assert "signed" not in captured
    assert captured["headers"]["Referer"].endswith("/list/42")
