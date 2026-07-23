"""Batch worker — resolve many URLs, emit per-video items.

Mirrors ``gui/threads/batch_worker.py`` (``BatchResolver.resolve_one`` per URL,
multi-P expansion) without Qt. Each resolved video becomes a ``BatchItemReady``
message the App enqueues; each failed URL becomes a ``BatchItemFailed``.
"""

from __future__ import annotations

import logging

from bilibili_downloader.tui import messages
from bilibili_downloader.tui.workers.base import CoreWorker

logger = logging.getLogger(__name__)


class BatchWorker(CoreWorker):
    def __init__(self, app, client, urls, flags: dict,
                 quality=None, codec=None,
                 creator_mid: int = 0, creator_name: str = ""):
        super().__init__(app)
        self._client = client
        self._urls = urls
        self._flags = flags
        self._quality = quality
        self._codec = codec
        self._creator_mid = creator_mid
        self._creator_name = creator_name

    def run(self) -> None:
        from bilibili_downloader.core.batch import BatchResolver
        from bilibili_downloader.core.models import DownloadItem

        resolver = BatchResolver(self._client)
        for url in self._urls:
            if self._cancel.is_set():
                break
            try:
                info = resolver.resolve_one(url)
                pages = [info.for_page(p) for p in info.pages] if info.is_multi_part else [info]
                for pi in pages:
                    item = DownloadItem(
                        video_info=pi,
                        selected_quality=self._quality,
                        selected_video_codec=self._codec,
                        download_danmaku=self._flags.get("danmaku", False),
                        download_subtitle=self._flags.get("subtitle", False),
                        download_metadata=self._flags.get("metadata", False),
                        download_cover=self._flags.get("cover", False),
                        download_comments=self._flags.get("comments", False),
                        embed_metadata=self._flags.get("embed_metadata", False),
                        embed_cover=self._flags.get("embed_cover", False),
                        creator_mid=self._creator_mid,
                        creator_name=self._creator_name,
                    )
                    self.emit(messages.BatchItemReady(item))
            except Exception as exc:  # noqa: BLE001
                logger.warning("batch resolve failed for %s: %s", url, exc)
                self.emit(messages.BatchItemFailed(f"无法解析 {url}: {exc}"))
        self.emit(messages.BatchDone())
