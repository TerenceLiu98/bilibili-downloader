"""BiliFlow terminal UI (Textual).

Launch via ``bilibili-downloader tui`` or ``python -m bilibili_downloader.tui``.
"""

from __future__ import annotations


def launch() -> None:
    """Create and run the BiliFlowTUI app."""
    from bilibili_downloader.tui.app import BiliFlowTUI

    BiliFlowTUI().run()


__all__ = ["launch"]
