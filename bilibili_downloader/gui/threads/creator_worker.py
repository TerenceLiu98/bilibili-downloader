"""Background creator-index fetch worker."""

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from bilibili_downloader.core.creator import (
    CreatorIndexService,
    creator_directory_name,
    save_creator_index,
)


class CreatorIndexWorker(QObject):
    progress = Signal(int, int)
    status = Signal(str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class CreatorIndexRunner(QRunnable):
    def __init__(self, worker, api_client, source, output_dir, resume_index=None):
        super().__init__()
        self._worker = worker
        self._api_client = api_client
        self._source = source
        self._output_dir = Path(output_dir)
        self._resume_index = resume_index
        self.setAutoDelete(True)

    def run(self):
        try:
            def save_checkpoint(index):
                path = (
                    self._output_dir
                    / creator_directory_name(index.name, index.mid)
                    / "index.partial.json"
                )
                save_creator_index(index, path)
                self._worker.status.emit(
                    f"已自动保存检查点：{len(index.videos)} 个投稿"
                )

            index = CreatorIndexService(self._api_client).fetch(
                self._source,
                progress_callback=self._worker.progress.emit,
                cancel_checker=lambda: self._worker.cancelled,
                status_callback=self._worker.status.emit,
                resume_index=self._resume_index,
                checkpoint_callback=save_checkpoint,
            )
            if not self._worker.cancelled:
                self._worker.finished.emit(index)
        except Exception as exc:  # noqa: BLE001
            if not self._worker.cancelled:
                self._worker.error.emit(str(exc))
