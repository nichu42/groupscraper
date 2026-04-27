"""
Attachment downloader for Google Groups messages.

Uses the authenticated Playwright browser session (``page.request``) to fetch
attachment and inline-image content, so Google's authentication cookies are
sent automatically.  When ``page.request`` fails with a network-layer error
(e.g. DNS resolution failure for third-party CDN hosts such as
``lh3.googleusercontent.com``), a fallback fetch is attempted via JavaScript
``fetch()`` running inside the browser, which uses Chrome's own networking
stack and DNS resolver.
"""
import asyncio
import base64
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
            # page.request uses Node.js DNS; third-party CDN hosts (e.g. lh3.googleusercontent.com)
            # can fail to resolve while the browser itself has no problem.  Try once more via
            # the browser's own JS fetch(), which uses Chrome's networking stack.
            if any(tag in str(e) for tag in ("ENOTFOUND", "ETIMEDOUT", "ECONNREFUSED", "net::")):
                logger.debug(f"  page.request network error, retrying via browser JS fetch")
                return await self._fetch_via_browser_js(attachment)
            logger.warning(f"  Failed to download {attachment.filename}: {e}")
            return attachment

    async def _fetch_via_browser_js(self, attachment: Attachment) -> Attachment:
        """Fetch *attachment* using the browser's JS ``fetch()`` as a fallback."""
        try:
            result = await self.page.evaluate("""async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                if (!r.ok) return {ok: false, status: r.status};
                const bytes = new Uint8Array(await r.arrayBuffer());
                let bin = '';
                for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                return {ok: true, contentType: r.headers.get('content-type') || '', data: btoa(bin)};
            }""", attachment.url)
            if not result.get("ok"):
                logger.warning(f"  Failed to download {attachment.filename}: HTTP {result.get('status')}")
                return attachment
            attachment.content = base64.b64decode(result["data"])
            ct = result.get("contentType", "")
            attachment.content_type = ct.split(";")[0].strip() if ct else (
                mimetypes.guess_type(attachment.filename)[0] or "application/octet-stream"
            )
            if attachment.inline:
                attachment.content_id = _make_content_id(attachment.url)
            logger.info(f"  Downloaded {attachment.filename} ({len(attachment.content)} bytes, {attachment.content_type}) [browser fetch]")
            if self.save_dir:
                safe_filename = self._sanitize_filename(attachment.filename)
                (self.save_dir / safe_filename).write_bytes(attachment.content)
        except Exception as e:
            logger.warning(f"  Failed to download {attachment.filename}: {e}")
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
        """Return a filename safe on Windows, macOS, and Linux."""
        # Replace characters forbidden on Windows (and / which is forbidden everywhere)
        for ch in r'<>:"/\|?*':
            filename = filename.replace(ch, "_")
        # Replace control characters
        filename = "".join("_" if ord(c) < 32 else c for c in filename)
        # Strip trailing dots and spaces (forbidden on Windows)
        filename = filename.rstrip(". ")
        # Prefix Windows reserved names (case-insensitive, with or without extension)
        _RESERVED = {"CON", "PRN", "AUX", "NUL",
                     "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
                     "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
        stem = filename.split(".")[0].upper()
        if stem in _RESERVED:
            filename = "_" + filename
        # Truncate to 200 chars to stay well clear of Windows MAX_PATH
        if len(filename) > 200:
            ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
            filename = filename[:200 - len(ext) - 1] + ("." + ext if ext else "")
        return filename or "_"
