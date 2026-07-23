"""Entry point for the Bilibili downloader application."""

import argparse
import logging
import sys

from bilibili_downloader.core.models import VideoQuality

logger = logging.getLogger(__name__)


def main():
    """Main entry point. Supports both CLI and GUI modes."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="bilibili-downloader",
        description="Bilibili video downloader — CLI and GUI modes.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- test subcommand ---
    test_parser = subparsers.add_parser(
        "test",
        help="Fetch video metadata for a BV/AV number or URL",
    )
    test_parser.add_argument(
        "source",
        help="BV/AV number, Bilibili URL, or b23.tv short link",
    )

    # --- download subcommand ---
    download_parser = subparsers.add_parser(
        "download",
        help="Download a video by BV/AV number or URL",
    )
    download_parser.add_argument(
        "source",
        help="BV/AV number, Bilibili URL, or b23.tv short link",
    )
    download_parser.add_argument(
        "--quality", "-q",
        type=int,
        default=VideoQuality.Q1080P,
        choices=[q.value for q in VideoQuality],
        help=f"Video quality code (default: {VideoQuality.Q1080P})",
    )
    download_parser.add_argument(
        "--output", "-o",
        help="Output directory (default: from settings or ./downloads)",
    )
    download_parser.add_argument(
        "--danmaku", "-d",
        action="store_true",
        help="Download danmaku (ASS format)",
    )
    download_parser.add_argument(
        "--subtitle", "-s",
        action="store_true",
        help="Download subtitles (SRT format)",
    )
    download_parser.add_argument(
        "--codec", "-c",
        type=int,
        choices=[7, 12, 13],
        help="Video codec code: 7=AVC, 12=HEVC, 13=AV1 (default: settings)",
    )
    download_parser.add_argument(
        "--page", "-p",
        default="1",
        help="Multi-part page number, or 'all' (default: 1)",
    )
    download_parser.add_argument(
        "--subtitle-language",
        default="zh-Hans",
        help="Preferred Bilibili subtitle language code (default: zh-Hans)",
    )
    _add_archive_options(download_parser, include_media=False)

    creator_parser = subparsers.add_parser(
        "creator",
        help="Fetch an UP creator's complete submission index",
    )
    creator_parser.add_argument("source", help="Creator UID or space.bilibili.com URL")
    creator_parser.add_argument("--index", help="Manifest path (default: creator directory/index.json)")
    creator_selection = creator_parser.add_mutually_exclusive_group()
    creator_selection.add_argument("--download-all", action="store_true")
    creator_selection.add_argument("--bvid", action="append", default=[])
    _add_download_options(creator_parser)
    _add_archive_options(creator_parser)

    index_parser = subparsers.add_parser(
        "download-index",
        help="Download selected videos from an existing creator index",
    )
    index_parser.add_argument("manifest", help="Path to index.json")
    index_selection = index_parser.add_mutually_exclusive_group(required=True)
    index_selection.add_argument("--all", action="store_true")
    index_selection.add_argument("--bvid", action="append", default=[])
    _add_download_options(index_parser)
    _add_archive_options(index_parser)

    args = parser.parse_args()

    if args.command == "test":
        _cli_test(args.source)
    elif args.command == "download":
        _cli_download(args)
    elif args.command == "creator":
        _cli_creator(args)
    elif args.command == "download-index":
        _cli_download_index(args)
    else:
        # Default: launch GUI
        _launch_gui()


def _cli_test(source: str):
    """CLI test: fetch video info for a given input."""

    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.batch import BatchResolver
    from bilibili_downloader.core.ffmpeg import FFmpegManager

    print(f"Fetching info for {source}...")

    # Check FFmpeg
    available, msg = FFmpegManager.check_available()
    print(f"FFmpeg: {'OK' if available else 'NOT FOUND'} - {msg}")

    # Fetch video info
    client = BilibiliAPIClient()
    try:
        info = BatchResolver(client).resolve_one(source)
        print(f"\nTitle:     {info.title}")
        print(f"Author:    {info.author}")
        print(f"Duration:  {info.duration_str}")
        print(f"BVID:      {info.bvid}")
        print(f"PID:       {info.cid}")
        print(f"Pages:     {len(info.pages)}")
        print(f"Subtitles: {len(info.subtitle_list)}")
        if info.subtitle_list:
            for s in info.subtitle_list:
                print(f"  - {s.lan}: {s.lan_doc}")
        print("\nSuccess!")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        client.close()


def _cli_download(args: argparse.Namespace):
    """CLI download: download a video by BV/AV number or URL."""
    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.batch import BatchResolver
    from bilibili_downloader.core.download_service import DownloadService
    from bilibili_downloader.core.models import DownloadItem
    from bilibili_downloader.utils.config import ConfigManager

    quality = VideoQuality(args.quality)

    # Load settings for default output dir and ffmpeg path
    config = ConfigManager()
    settings = config.load()
    output_dir = args.output or settings.output_dir

    print(f"Downloading {args.source} at {quality.label}...")

    client = BilibiliAPIClient(sessdata=settings.sessdata or None)
    service = None
    try:
        info = BatchResolver(client).resolve_one(args.source)
        print(f"Title: {info.title}")

        if str(args.page).lower() == "all":
            page_infos = [info.for_page(page) for page in info.pages] or [info]
        else:
            try:
                page_number = int(args.page)
            except ValueError as exc:
                raise ValueError("--page must be a positive page number or 'all'") from exc
            if page_number < 1 or page_number > max(1, len(info.pages)):
                raise ValueError(f"--page must be between 1 and {max(1, len(info.pages))}")
            page_infos = [info.for_page(info.pages[page_number - 1])] if info.pages else [info]

        def progress(pct, text):
            bar_len = 30
            filled = int(bar_len * pct)
            bar = "=" * filled + "-" * (bar_len - filled)
            print(f"\r[{bar}] {pct * 100:5.1f}%  {text}", end="", flush=True)

        service = DownloadService(
            client, output_dir, ffmpeg_path=settings.ffmpeg_path or None,
        )
        codec = args.codec or settings.default_video_codec
        for index, page_info in enumerate(page_infos, start=1):
            if len(page_infos) > 1:
                print(f"\n[{index}/{len(page_infos)}] CID {page_info.cid}")
            item = DownloadItem(
                video_info=page_info,
                selected_quality=quality,
                selected_video_codec=codec,
                output_path=output_dir,
                download_danmaku=args.danmaku,
                download_subtitle=args.subtitle,
                selected_subtitle_lan=args.subtitle_language,
                download_metadata=_option(args, "metadata", settings.download_metadata),
                download_cover=_option(args, "cover", settings.download_cover),
                download_comments=_option(args, "comments", settings.download_comments),
                embed_metadata=_option(args, "embed_metadata", settings.embed_metadata),
                embed_cover=_option(args, "embed_cover", settings.embed_cover),
                refresh_sidecars=getattr(args, "refresh_sidecars", False),
            )
            outcome = service.download(item, progress)
            print(f"\nSaved to: {outcome.video_path}")
            for warning in outcome.warnings:
                print(f"Warning: {warning}")
    except KeyboardInterrupt:
        if service is not None:
            service.cancel()
        print("\nDownload cancelled")
        raise SystemExit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        client.close()


def _add_download_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--quality", "-q", type=int, default=VideoQuality.Q1080P,
        choices=[q.value for q in VideoQuality],
    )
    parser.add_argument("--output", "-o")
    parser.add_argument("--codec", "-c", type=int, choices=[7, 12, 13])
    parser.add_argument("--subtitle-language", default="zh-Hans")


def _add_archive_options(
    parser: argparse.ArgumentParser, *, include_media: bool = True
) -> None:
    if include_media:
        parser.add_argument("--danmaku", action=argparse.BooleanOptionalAction, default=None)
        parser.add_argument("--subtitle", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--metadata", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--cover", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--comments", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--embed-metadata", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument(
        "--embed-cover", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument(
        "--refresh-sidecars",
        action="store_true",
        help="Overwrite existing metadata, comments, cover, danmaku and subtitles",
    )


def _option(args: argparse.Namespace, name: str, default: bool) -> bool:
    value = getattr(args, name, None)
    return default if value is None else bool(value)


def _cli_creator(args: argparse.Namespace) -> None:
    from pathlib import Path

    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.creator import (
        CreatorIndexService,
        creator_directory_name,
        save_creator_index,
    )
    from bilibili_downloader.utils.config import ConfigManager

    settings = ConfigManager().load()
    output_dir = Path(args.output or settings.output_dir)
    client = BilibiliAPIClient(sessdata=settings.sessdata or None)
    try:
        print(f"Fetching creator index for {args.source}...")
        index = CreatorIndexService(client).fetch(
            args.source,
            progress_callback=lambda done, total: print(
                f"\rIndexed {done}/{total or '?'} videos", end="", flush=True
            ),
            status_callback=lambda status: print(f"\n{status}", flush=True),
        )
        manifest = Path(args.index) if args.index else (
            output_dir / creator_directory_name(index.name, index.mid) / "index.json"
        )
        save_creator_index(index, manifest)
        print(f"\nIndex saved to: {manifest}")
        print(f"Creator: {index.name} ({index.mid}), videos: {len(index.videos)}")
        if args.download_all or args.bvid:
            selected = index.videos if args.download_all else _select_entries(index, args.bvid)
            _download_creator_entries(client, settings, output_dir, index, selected, args)
    except KeyboardInterrupt:
        print("\nCancelled")
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\nError: {exc}")
        raise SystemExit(1) from exc
    finally:
        client.close()


def _cli_download_index(args: argparse.Namespace) -> None:
    from pathlib import Path

    from bilibili_downloader.api.client import BilibiliAPIClient
    from bilibili_downloader.core.creator import load_creator_index
    from bilibili_downloader.utils.config import ConfigManager

    settings = ConfigManager().load()
    output_dir = Path(args.output or settings.output_dir)
    index = load_creator_index(Path(args.manifest))
    selected = index.videos if args.all else _select_entries(index, args.bvid)
    client = BilibiliAPIClient(sessdata=settings.sessdata or None)
    try:
        _download_creator_entries(client, settings, output_dir, index, selected, args)
    except KeyboardInterrupt:
        print("\nCancelled")
        raise SystemExit(130)
    finally:
        client.close()


def _select_entries(index, bvids: list[str]):
    wanted = {value.upper() for value in bvids}
    selected = [entry for entry in index.videos if entry.bvid.upper() in wanted]
    missing = wanted - {entry.bvid.upper() for entry in selected}
    if missing:
        raise ValueError(f"index.json 中不存在：{', '.join(sorted(missing))}")
    return selected


def _download_creator_entries(client, settings, output_dir, index, entries, args) -> None:
    from bilibili_downloader.core.download_service import DownloadService
    from bilibili_downloader.core.models import DownloadItem

    service = DownloadService(
        client, str(output_dir), ffmpeg_path=settings.ffmpeg_path or None
    )
    quality = VideoQuality(args.quality)
    codec = args.codec or settings.default_video_codec
    total = len(entries)
    for position, entry in enumerate(entries, start=1):
        print(f"\n[{position}/{total}] {entry.bvid} {entry.title}")
        info = client.get_video_info(entry.bvid)
        pages = [info.for_page(page) for page in info.pages] or [info]
        for page_info in pages:
            item = DownloadItem(
                video_info=page_info,
                selected_quality=quality,
                selected_video_codec=codec,
                download_danmaku=_option(args, "danmaku", settings.download_danmaku),
                download_subtitle=_option(args, "subtitle", settings.download_subtitle),
                download_metadata=_option(args, "metadata", settings.download_metadata),
                download_cover=_option(args, "cover", settings.download_cover),
                download_comments=_option(args, "comments", settings.download_comments),
                embed_metadata=_option(args, "embed_metadata", settings.embed_metadata),
                embed_cover=_option(args, "embed_cover", settings.embed_cover),
                refresh_sidecars=args.refresh_sidecars,
                selected_subtitle_lan=args.subtitle_language,
                creator_mid=index.mid,
                creator_name=index.name,
            )
            outcome = service.download(item, _print_progress)
            print(f"\nSaved to: {outcome.video_path}")
            for warning in outcome.warnings:
                print(f"Warning: {warning}")


def _print_progress(pct: float, text: str) -> None:
    bar_len = 30
    filled = int(bar_len * pct)
    bar = "=" * filled + "-" * (bar_len - filled)
    print(f"\r[{bar}] {pct * 100:5.1f}%  {text}", end="", flush=True)


def _launch_gui():
    """Launch the PySide6 GUI application."""
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from bilibili_downloader.gui.main_window import MainWindow
        from bilibili_downloader.gui.resources.paths import asset_path
        from bilibili_downloader.gui.resources.theme import ThemeManager
    except ImportError:
        print("PySide6 not installed. Install with: pip install PySide6")
        print("Or use CLI mode: python -m bilibili_downloader test <BV_number>")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("Bilibili Downloader")
    app.setOrganizationName("bilibili-downloader")
    app.setWindowIcon(QIcon(asset_path("app_icon.png")))
    theme_manager = ThemeManager(app)

    window = MainWindow()
    window.show()

    # Keep the controller alive for system theme change notifications.
    app._theme_manager = theme_manager
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
