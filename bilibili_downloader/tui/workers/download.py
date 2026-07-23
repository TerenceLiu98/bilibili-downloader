"""Download worker — runs one download on a thread.

Each download gets its OWN ``DownloadService`` instance because the service
holds mutable per-download state (``self._cancelled``, ``downloader.last_*``);
they share the httpx client (thread-safe) and the module-level
``_SIDECAR_LOCK``. Mirrors ``gui/threads/download_worker.py`` semantics
without Qt.
"""

from __future__ import annotations

import logging

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.workers.base import CoreWorker

logger = logging.getLogger(__name__)


class DownloadWorker(CoreWorker):
    def __init__(self, app, client, item, output_dir: str, download_id: int,
                 ffmpeg_path: str | None = None):
        super().__init__(app)
        self._client = client
        self._item = item
        self._output_dir = output_dir
        self._download_id = download_id
        self._ffmpeg_path = ffmpeg_path
        self._service = None  # built in run()

    def run(self) -> None:
        import os

        from bilibili_downloader.core.download_service import DownloadService

        try:
            os.makedirs(self._output_dir, exist_ok=True)
            self._service = DownloadService(
                self._client, self._output_dir, ffmpeg_path=self._ffmpeg_path
            )

            def progress_cb(pct: float, text: str) -> None:
                if self._cancel.is_set():
                    self._service.cancel()
                    raise RuntimeError("cancel")
                self.emit(messages.DownloadProgress(self._download_id, pct, text))

            outcome = self._service.download(self._item, progress_cb)
            if self._cancel.is_set():
                self.emit(messages.DownloadCancelled(self._download_id))
            else:
                self.emit(messages.DownloadFinished(self._download_id, outcome))
        except Exception as exc:  # noqa: BLE001
            if self._cancel.is_set() or "cancel" in str(exc).lower():
                self.emit(messages.DownloadCancelled(self._download_id))
            else:
                logger.error("download %s failed: %s", self._download_id, exc)
                self.emit(messages.DownloadFailed(self._download_id, str(exc)))

    def cancel(self) -> None:  # type: ignore[override]
        super().cancel()
        if self._service is not None:
            self._service.cancel()
