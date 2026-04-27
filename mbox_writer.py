"""
MBOX writer for scraped Google Groups messages.

Converts :class:`~thread_fetcher.Message` objects to RFC 2822
:class:`~email.message.EmailMessage` instances and appends them to a
standard MBOX file (RFC 4155) readable by Thunderbird, Apple Mail, and
most archival tools.
"""
import logging
import hashlib
import time
from io import BytesIO
from pathlib import Path
from email.message import EmailMessage
from email.generator import BytesGenerator
from email.utils import formatdate, parsedate_to_datetime

from thread_fetcher import Message

logger = logging.getLogger(__name__)


def _to_mbox_date(rfc2822: str) -> str:
    """Convert an RFC 2822 date string to the ctime format required by the MBOX From_ line."""
    try:
        return parsedate_to_datetime(rfc2822).strftime("%a %b %d %H:%M:%S %Y")
    except Exception:
        return time.strftime("%a %b %d %H:%M:%S %Y")


def _make_content_id(url: str) -> str:
    """Return a stable ``Content-ID`` token derived from *url* (MD5, first 16 hex chars)."""
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    return f"{h}@groups.google.com"


class MboxWriter:
    """Append :class:`~thread_fetcher.Message` objects to an MBOX file."""

    def __init__(self, mbox_path: Path):
        """
        Args:
            mbox_path: Destination file.  Created (along with any missing
                parent directories) on the first :meth:`write_messages` call.
        """
        self.mbox_path = mbox_path
        self.mbox_path.parent.mkdir(parents=True, exist_ok=True)

    def write_messages(self, messages: list[Message]):
        """
        Append *messages* to the MBOX file.

        Each message is converted via :meth:`_construct_email`.  The file is
        opened in append mode so repeated calls accumulate messages safely.

        Args:
            messages: Messages to write; no-op if the list is empty.
        """
        if not messages:
            logger.info("No messages to write")
            return

        try:
            with open(self.mbox_path, "ab") as f:
                for msg in messages:
                    email_msg = self._construct_email(msg)

                    # RFC 4155 envelope From_ line: "From addr ctime\n"
                    addr = msg.sender
                    if "<" in addr and ">" in addr:
                        addr = addr.split("<")[1].split(">")[0].strip()
                    envelope_date = _to_mbox_date(msg.date)
                    envelope = f"From {addr} {envelope_date}\n"
                    f.write(envelope.encode("ascii", errors="replace"))

                    buf = BytesIO()
                    BytesGenerator(buf, mangle_from_=True).flatten(email_msg)
                    data = buf.getvalue()
                    f.write(data)
                    if not data.endswith(b"\n"):
                        f.write(b"\n")
                    f.write(b"\n")  # blank line between messages

            logger.info(f"Wrote {len(messages)} messages to {self.mbox_path}")

        except Exception as e:
            logger.error(f"Failed to write MBOX: {e}", exc_info=True)
            raise

    def _construct_email(self, msg: Message) -> EmailMessage:
        """
        Convert a :class:`~thread_fetcher.Message` to a fully-formed
        :class:`~email.message.EmailMessage`.

        Structure produced:

        - Text-only body → ``text/plain``
        - Body + HTML → ``multipart/alternative`` (plain + html)
        - Body + attachments → ``multipart/mixed`` containing the above plus
          each attachment as a ``application/*`` or ``image/*`` part

        Inline images with downloaded content are rewritten from their original
        Google-hosted URL to a ``cid:`` reference so mail clients render them
        inline.  A synthetic ``Message-ID`` is generated from an MD5 of the
        message fields to avoid duplicates in the MBOX.

        Args:
            msg: Source message.

        Returns:
            Populated :class:`~email.message.EmailMessage` ready to be added
            to the MBOX.
        """
        email = EmailMessage()

        email["From"] = msg.sender
        if msg.recipients:
            email["To"] = ", ".join(msg.recipients)
        email["Subject"] = msg.subject

        if msg.date:
            email["Date"] = msg.date
        else:
            email["Date"] = formatdate(localtime=True)

        domain = msg.sender.split("@")[-1] if "@" in msg.sender else "groups.google.com"
        msg_hash = hashlib.md5(
            f"{msg.sender}{msg.subject}{msg.date}{msg.body}".encode()
        ).hexdigest()[:8]
        email["Message-ID"] = f"<{msg_hash}@{domain}>"

        html_body = msg.body_html
        if html_body and msg.attachments:
            for att in msg.attachments:
                if att.inline and att.content and att.content_id:
                    # Replace the original URL with cid: reference
                    # The URL in HTML may have &amp; encoded
                    escaped_url = att.url.replace("&", "&amp;")
                    html_body = html_body.replace(escaped_url, f"cid:{att.content_id}")
                    html_body = html_body.replace(att.url, f"cid:{att.content_id}")

        email.set_content(msg.body)
        if html_body:
            email.add_alternative(html_body, subtype="html")

        # add_attachment() promotes the message to multipart/mixed automatically
        if msg.attachments:
            for att in msg.attachments:
                if not att.content:
                    logger.warning(f"Attachment '{att.filename}' has no content, skipping")
                    continue

                maintype, subtype = att.content_type.split("/", 1) if "/" in att.content_type else ("application", "octet-stream")

                email.add_attachment(
                    att.content,
                    maintype=maintype,
                    subtype=subtype,
                    filename=att.filename,
                )

                if att.inline and att.content_id:
                    parts = list(email.iter_parts())
                    if parts:
                        last_part = parts[-1]
                        last_part["Content-ID"] = f"<{att.content_id}>"
                        last_part.replace_header(
                            "Content-Disposition",
                            f'inline; filename="{att.filename}"'
                        )

        return email
