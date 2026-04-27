"""
Group Scraper: Export mailing list threads to MBOX format.
Copyright (C) 2026 nichu42 and contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

Main entry point.  Orchestrates authentication, thread crawling, message
fetching, and MBOX export for a Google Groups mailing list.

Usage::

    python scraper.py [group@domain.com] [--reauth] [--debug] [--limit N]
                      [--attachments | --no-attachments]
                      [--page-load-wait SECONDS]
"""
import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path
from datetime import datetime

from auth import ensure_session
from crawler import get_all_thread_urls, SessionExpiredError
from thread_fetcher import ThreadFetcher
from attachment_downloader import AttachmentDownloader
from progress import ProgressTracker
from mbox_writer import MboxWriter


def _setup_logging():
    """Configure logging with colored WARNING/ERROR output when writing to a terminal."""

    # Enable ANSI escape codes on Windows (no-op on macOS/Linux where it's always on)
    if sys.platform == "win32":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass

    use_color = sys.stderr.isatty() or sys.stdout.isatty()

    class _ColoredFormatter(logging.Formatter):
        _YELLOW = "\033[33m"
        _RED    = "\033[31m"
        _RESET  = "\033[0m"
        _COLORS = {logging.WARNING: _YELLOW, logging.ERROR: _RED, logging.CRITICAL: _RED}

        def format(self, record):
            msg = super().format(record)
            color = self._COLORS.get(record.levelno, "")
            return f"{color}{msg}{self._RESET}" if color else msg

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(_ColoredFormatter(fmt) if use_color else logging.Formatter(fmt))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


_setup_logging()
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(
        description="Export all messages from a Google Group to MBOX format"
    )
    parser.add_argument("group_email", nargs="?", help="Group email (group@domain.com)")
    parser.add_argument("--reauth", action="store_true", help="Force re-authentication")
    parser.add_argument("--debug", action="store_true", help="Save debug files and enable verbose logging")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N pending threads (0 = all)")
    parser.add_argument("--page-load-wait", type=float, default=4.0, metavar="SECONDS", help="Fallback wait (seconds) after page load if thread selector not found (default: 4)")
    parser.add_argument("--attachments", action="store_true", help="Download and embed attachments in MBOX")
    parser.add_argument("--no-attachments", action="store_true", help="Skip attachments (no prompt)")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Prompt for group email if not provided
    if not args.group_email:
        try:
            args.group_email = input("Enter group email (e.g., group@domain.com): ").strip()
        except EOFError:
            parser.error("No group email provided. Pass it as an argument: scraper.py group@domain.com")

    # Split into group and domain
    if "@" in args.group_email:
        group, domain = args.group_email.split("@", 1)
    else:
        group = args.group_email
        try:
            domain = input("Enter domain (e.g., workspacedomain.com): ").strip()
        except EOFError:
            parser.error("No domain provided. Pass the full email as an argument: scraper.py groupname@domain.com")

    # Prompt for attachments if neither flag was set
    save_attachments = args.attachments
    if not args.attachments and not args.no_attachments:
        answer = input("Download and embed attachments in MBOX? [y/N]: ").strip().lower()
        save_attachments = answer in ("y", "yes")

    # Setup export directory structure
    export_base = Path("exports") / f"{domain}__{group}"
    today = datetime.now().strftime("%Y-%m-%d")
    export_dir = export_base / today
    export_dir.mkdir(parents=True, exist_ok=True)

    debug_dir = export_dir / "debug" if args.debug else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Debug mode enabled. Files will be saved to {debug_dir}")

    # Setup progress tracking and output writers
    progress_file = export_dir / "progress.json"
    progress = ProgressTracker(progress_file)
    mbox_path = export_dir / "messages.mbox"
    writer = MboxWriter(mbox_path)

    # Construct group URL
    group_url = f"https://groups.google.com/a/{domain}/g/{group}"

    # Step 1: Authentication
    logger.info("Step 1: Authentication")
    page, context, browser, playwright = await ensure_session(group_url, reauth=args.reauth)

    try:
        # Step 2: Enumerate all thread URLs from the forum list
        logger.info("Step 2: Crawling forum list for thread URLs")
        try:
            thread_urls = await get_all_thread_urls(page, domain, group, page_load_wait=args.page_load_wait)
        except SessionExpiredError:
            logger.warning("Session expired — starting automatic re-authentication...")
            await context.close()
            await browser.close()
            await playwright.stop()
            page, context, browser, playwright = await ensure_session(group_url, reauth=True)
            thread_urls = await get_all_thread_urls(page, domain, group, page_load_wait=args.page_load_wait)
        logger.info(f"Found {len(thread_urls)} total threads")

        # Filter to pending threads
        pending_urls = progress.get_pending(thread_urls)
        if args.limit:
            pending_urls = pending_urls[:args.limit]
            logger.info(f"Processing {len(pending_urls)} pending threads (--limit {args.limit})")
        else:
            logger.info(f"Processing {len(pending_urls)} pending threads")

        # Step 3: Fetch messages from each thread
        logger.info("Step 3: Fetching messages from threads")
        thread_fetcher = ThreadFetcher(page, debug_dir=debug_dir)

        # Setup attachment downloader if requested
        att_downloader = None
        if save_attachments:
            att_save_dir = export_dir / "attachments"
            att_downloader = AttachmentDownloader(page, save_dir=att_save_dir)
            logger.info(f"Attachments will be downloaded to {att_save_dir}")

        threads_ok = 0
        threads_failed = 0
        total_messages = 0
        total_attachments = 0
        total_att_failed = 0

        for i, thread_url in enumerate(pending_urls, 1):
            logger.info(f"[{i}/{len(pending_urls)}] Processing {thread_url}")
            try:
                messages = await thread_fetcher.fetch_messages(thread_url)
                logger.info(f"  → Extracted {len(messages)} messages")

                # Download attachments if enabled
                if att_downloader and messages:
                    for msg in messages:
                        if msg.attachments:
                            logger.info(f"  → {len(msg.attachments)} attachment(s) found")
                            downloaded = await att_downloader.download_all(msg.attachments)
                            total_attachments += sum(1 for a in downloaded if a.content)
                            total_att_failed += sum(1 for a in downloaded if not a.content)

                # Write to MBOX
                if messages:
                    writer.write_messages(messages)
                    total_messages += len(messages)

                # Mark as completed
                progress.mark_done(thread_url)
                threads_ok += 1

            except Exception as e:
                logger.error(f"  → Failed to fetch: {e}")
                threads_failed += 1
                if args.debug:
                    logger.info(f"  → Check {debug_dir} for debug files")

        # Cleanup: remove attachments folder and progress.json after a successful complete run
        complete = not progress.get_pending(thread_urls)
        if complete:
            att_dir = export_dir / "attachments"
            if att_dir.exists():
                shutil.rmtree(att_dir)
                logger.info(f"Removed attachments folder: {att_dir}")
            if progress_file.exists():
                progress_file.unlink()
                logger.info(f"Removed progress file: {progress_file}")

        if args.debug:
            logger.info(f"Debug files saved to: {debug_dir}")

        # ── Summary ──────────────────────────────────────────────────────────
        logger.info("")
        logger.info("━" * 60)
        logger.info("  SUMMARY")
        logger.info("━" * 60)
        logger.info(f"  Threads processed : {threads_ok + threads_failed}")
        logger.info(f"  Successful        : {threads_ok}")
        if threads_failed:
            logger.warning(f"  Failed            : {threads_failed}")
        logger.info(f"  Messages written  : {total_messages}")
        if save_attachments:
            logger.info(f"  Attachments dl'd  : {total_attachments}")
            if total_att_failed:
                logger.warning(f"  Attachments failed: {total_att_failed}")
        logger.info(f"  Output            : {mbox_path}")
        if not complete:
            logger.warning(f"  Run incomplete — resume by running the script again")
        logger.info("━" * 60)

    finally:
        await context.close()
        await browser.close()
        await playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
