"""
Attachment downloader for Google Groups messages.

Uses the authenticated Playwright browser session (``page.request``) to fetch
attachment and inline-image content, so Google's authentication cookies are
sent automatically.
"""
import asyncio
import logging
import mimetypes
from pathlib import Path

from thread_fetcher import Attachment
from mbox_writer import _make_content_id

logger = logging.getLogger(__name__)


class AttachmentDownloader:
    """Download attachments using the authenticated Playwright browser session."""

    def __init__(self, page, save_dir: Path | None = None):
        """
        Args:
            page: Authenticated Playwright page object.
            save_dir: Optional directory to save attachment files to disk.
        """
        self.page = page
        self.save_dir = save_dir
        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, attachment: Attachment) -> Attachment:
        """
        Download *attachment* and populate its ``content`` and ``content_type``.

        The content type is taken from the ``Content-Type`` response header when
        present, otherwise guessed from the filename.  For inline images,
        ``content_id`` is also set from the URL so it can be referenced with
        ``cid:`` in the MBOX HTML part.

        Args:
            attachment: Attachment whose ``url`` will be fetched.

        Returns:
            The same ``Attachment`` instance with fields populated in-place.
            Returns the original (with empty ``content``) on HTTP or network error.
        """
        logger.info(f"  Downloading attachment: {attachment.filename}")

        try:
            response = await self.page.request.get(attachment.url)

            if response.status != 200:
                logger.warning(f"  Failed to download {attachment.filename}: HTTP {response.status}")
                return attachment

            attachment.content = await response.body()

            content_type = response.headers.get("content-type", "")
            if content_type:
                attachment.content_type = content_type.split(";")[0].strip()
            else:
                guessed, _ = mimetypes.guess_type(attachment.filename)
                attachment.content_type = guessed or "application/octet-stream"

            if attachment.inline:
                attachment.content_id = _make_content_id(attachment.url)

            logger.info(f"  Downloaded {attachment.filename} ({len(attachment.content)} bytes, {attachment.content_type})")

            if self.save_dir:
                safe_filename = self._sanitize_filename(attachment.filename)
                file_path = self.save_dir / safe_filename
                file_path.write_bytes(attachment.content)
                logger.info(f"  Saved to {file_path}")

            return attachment

        except Exception as e:
            logger.error(f"  Failed to download {attachment.filename}: {e}")
            return attachment

    async def download_all(self, attachments: list[Attachment]) -> list[Attachment]:
        """
        Download every attachment in *attachments* sequentially.

        Sequential (rather than concurrent) to avoid triggering Google's
        rate limits.  A small delay is inserted between requests.

        Args:
            attachments: List of attachments to download.

        Returns:
            The same list with each item updated in-place by :meth:`download`.
        """
        results = []
        for att in attachments:
            downloaded = await self.download(att)
            results.append(downloaded)
            await asyncio.sleep(0.3)
        return results

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Remove or replace characters unsafe for filenames."""
        unsafe = '<>:"/\\|?*'
        for ch in unsafe:
            filename = filename.replace(ch, "_")
        return filename
