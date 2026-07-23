"""Tests for TUI worker classes (plain objects, no Textual runtime needed).

Each worker emits Textual Messages via ``app.post_message``. We inject a fake
app whose ``post_message`` records messages, then assert the right message
types fire for success / failure / cancel paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from bilibili_downloader.core.models import (
    DownloadOutcome,
    VideoInfo,
    VideoQuality,
)
from bilibili_downloader.tui import messages


class FakeApp:
    """Captures posted messages instead of routing them to a Textual loop."""

    def __init__(self):
        self.posted: list = []

    def post_message(self, message):
        self.posted.append(message)


def _make_download_item():
    return MagicMock(
        video_info=VideoInfo(bvid="BV1TEST00001", cid=1, title="t", duration=10),
        selected_quality=VideoQuality.Q1080P,
        selected_video_codec=7,
        filename="x.mp4",
    )


def test_download_worker_emits_finished_on_success(tmp_path):
    from bilibili_downloader.tui.workers.download import DownloadWorker

    app = FakeApp()
    fake_service = MagicMock()
    fake_service.download.return_value = DownloadOutcome(video_path=str(tmp_path / "x.mp4"))

    with patch(
        "bilibili_downloader.core.download_service.DownloadService", return_value=fake_service
    ):
        worker = DownloadWorker(app, MagicMock(), _make_download_item(), str(tmp_path), 5)
        worker.run()

    msg_types = [type(m).__name__ for m in app.posted]
    assert "DownloadFinished" in msg_types
    finished = next(m for m in app.posted if isinstance(m, messages.DownloadFinished))
    assert finished.download_id == 5


def test_download_worker_emits_failed_on_error(tmp_path):
    from bilibili_downloader.tui.workers.download import DownloadWorker

    app = FakeApp()
    fake_service = MagicMock()
    fake_service.download.side_effect = RuntimeError("network down")

    with patch(
        "bilibili_downloader.core.download_service.DownloadService", return_value=fake_service
    ):
        worker = DownloadWorker(app, MagicMock(), _make_download_item(), str(tmp_path), 1)
        worker.run()

    msg_types = [type(m).__name__ for m in app.posted]
    assert "DownloadFailed" in msg_types
    assert "DownloadCancelled" not in msg_types


def test_download_worker_emits_cancelled_when_cancelled(tmp_path):
    from bilibili_downloader.tui.workers.download import DownloadWorker

    app = FakeApp()
    fake_service = MagicMock()

    def download(item, cb):
        # Simulate core noticing the cancel flag.
        worker.cancel()
        raise RuntimeError("cancel")

    fake_service.download.side_effect = download

    with patch(
        "bilibili_downloader.core.download_service.DownloadService", return_value=fake_service
    ):
        worker = DownloadWorker(app, MagicMock(), _make_download_item(), str(tmp_path), 9)
        worker.run()

    msg_types = [type(m).__name__ for m in app.posted]
    assert "DownloadCancelled" in msg_types
    assert "DownloadFailed" not in msg_types


def test_resolve_worker_handles_playurl_failure():
    from bilibili_downloader.tui.workers.resolve import ResolveWorker

    app = FakeApp()
    info = VideoInfo(bvid="BV1TEST00002", cid=2, title="t", duration=10)
    client = MagicMock()

    with patch(
        "bilibili_downloader.core.batch.BatchResolver.resolve_one", return_value=info
    ), patch.object(
        client, "get_play_url", side_effect=RuntimeError("no playurl")
    ):
        ResolveWorker(app, client, "BV1TEST00002").run()

    finished = next(m for m in app.posted if isinstance(m, messages.ResolveFinished))
    assert finished.playurl_ok is False
    assert finished.info.bvid == "BV1TEST00002"
