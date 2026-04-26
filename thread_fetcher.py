"""
Google Groups thread fetcher and message parser.

Navigates to individual thread pages and extracts messages from the
``ds:11`` server-side data block that Google Groups embeds in the page
HTML.  Attachment and inline-image metadata is parsed from the HTML body
of each message.
"""
import asyncio
import logging
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


@dataclass
class Attachment:
    """A file or inline image attached to a message."""

    filename: str
    url: str
    content: bytes = b""
    content_type: str = "application/octet-stream"
    inline: bool = False  # True for <img> embeds; False for downloadable files
    content_id: str = ""  # Populated after download; used for cid: references in HTML


@dataclass
class Message:
    """A single email message extracted from a Google Groups thread."""

    sender: str     # RFC 2822 ``From`` value, e.g. ``"Alice <alice@example.com>"``
    date: str       # RFC 2822 date string
    subject: str
    body: str       # Plain-text version derived from body_html
    body_html: str = ""
    attachments: list = None

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []


def parse_attachments_from_html(html: str) -> list[Attachment]:
    """
    Extract downloadable file attachment metadata from message HTML.

    Matches tags that carry ``data-view-attachment-url`` and an
    ``aria-label="Download file <name>"`` attribute, e.g.::

        <div class="E3gXse"
             data-view-attachment-url="...?part=0.1&amp;view=1"
             aria-label="Download file report.pdf">

    Attribute order within the tag is not assumed.

    Args:
        html: Raw HTML of a single message body.

    Returns:
        List of :class:`Attachment` objects (content not yet downloaded).
    """
    attachments = []
    # Find every tag that contains data-view-attachment-url (attributes may be in any order)
    for tag_match in re.finditer(r'<[^>]*data-view-attachment-url="[^"]*"[^>]*>', html, re.DOTALL):
        tag_html = tag_match.group(0)
        url_m = re.search(r'data-view-attachment-url="([^"]+)"', tag_html)
        label_m = re.search(r'aria-label="Download file ([^"]+)"', tag_html)
        if url_m and label_m:
            url = url_m.group(1).replace("&amp;", "&")
            filename = label_m.group(1)
            attachments.append(Attachment(filename=filename, url=url))
    return attachments


def parse_inline_images_from_html(html: str) -> list[Attachment]:
    """
    Extract inline image metadata from message HTML.

    Only keeps ``<img>`` tags whose ``src`` resolves to a Google-hosted URL:

    - ``*.googleusercontent.com`` (lh3, ci3, mail-attachment, proxy, …)
    - ``groups.google.com/…/attach/…``
    - ``*.googleapis.com``

    Args:
        html: Raw HTML of a single message body.

    Returns:
        List of :class:`Attachment` objects with ``inline=True``
        (content not yet downloaded).
    """
    attachments = []
    # Capture the full <img ...> tag (src may appear at any position within the tag)
    for tag_match in re.finditer(r'<img\b[^>]+>', html, re.IGNORECASE | re.DOTALL):
        tag_html = tag_match.group(0)
        src_m = re.search(r'\bsrc="([^"]+)"', tag_html, re.IGNORECASE)
        if not src_m:
            continue
        url = src_m.group(1).replace("&amp;", "&")
        # Only keep Google-hosted image URLs
        if not re.search(
            r'https?://[^"]*(?:googleusercontent\.com|groups\.google\.com/[^"]*attach|googleapis\.com)',
            url, re.IGNORECASE
        ):
            continue

        url_path = url.split("?")[0]
        filename = url_path.rsplit("/", 1)[-1] if "/" in url_path else "image"

        alt_m = re.search(r'\balt="([^"]*)"', tag_html, re.IGNORECASE)
        if alt_m and alt_m.group(1):
            filename = alt_m.group(1)

        attachments.append(Attachment(filename=filename, url=url, inline=True))
    return attachments


class _HTMLToText(HTMLParser):
    """Convert HTML to readable plain text, preserving document structure."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._tag_stack = []
        self._in_pre = False
        self._href_stack = []  # track <a> href values

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self._tag_stack.append(tag)
        attrs_dict = dict(attrs)

        if tag == "br":
            self._parts.append("\n")
        elif tag == "hr":
            self._parts.append("\n────────\n")
        elif tag == "li":
            self._parts.append("\n• ")
        elif tag in ("p", "div", "tr"):
            self._parts.append("\n\n")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n\n")
        elif tag == "blockquote":
            self._parts.append("\n> ")
        elif tag == "pre":
            self._in_pre = True
            self._parts.append("\n\n")
        elif tag == "a":
            href = attrs_dict.get("href", "")
            self._href_stack.append(href)
        elif tag == "img":
            alt = attrs_dict.get("alt", "")
            if alt:
                self._parts.append(f"[{alt}]")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if tag == "pre":
            self._in_pre = False
            self._parts.append("\n\n")
        elif tag == "blockquote":
            self._parts.append("\n")
        elif tag == "a" and self._href_stack:
            href = self._href_stack.pop()
            # Show URL in parentheses after link text
            if href and not href.startswith("javascript:") and not href.startswith("#"):
                self._parts.append(f" ({href})")

    def handle_data(self, data):
        if self._in_pre:
            # Preserve whitespace inside <pre>
            self._parts.append(data)
        else:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Normalize whitespace: collapse runs of spaces (but preserve newlines)
        lines = text.split("\n")
        lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in lines]
        text = "\n".join(lines)
        # Collapse 3+ blank lines to 2
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


def _html_to_text(html_str: str) -> str:
    """Convert HTML to readable plain text preserving structure."""
    stripper = _HTMLToText()
    stripper.feed(html_str)
    return stripper.get_text()


def _ts_to_rfc2822(ts_pair) -> str:
    """Convert Google's ``[seconds, nanos]`` timestamp to an RFC 2822 date string."""
    try:
        ts = ts_pair[0]
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
    except Exception:
        return ""


def _parse_ds11(data_str: str) -> list[Message]:
    """
    Deserialise the ``ds:11`` JSON payload embedded in a Google Groups thread page.

    Confirmed structure (from live inspection)::

        data[0]           = [group_id, group_email]
        data[1]           = thread summary
        data[2]           = list of message entries
        data[2][i][0][0]  = metadata: [group_id, msg_id, sender_array, 0, None,
                                        subject, body_preview, [ts_secs, ts_nanos], …]
        data[2][i][0][1]  = body:     [2, [[1, [None, "<html body>"]]]]

        sender_array = [[name, avatar_url, email, user_id], …]  (first entry = From)

    Args:
        data_str: The raw JSON string extracted from the ``AF_initDataCallback`` call.

    Returns:
        List of :class:`Message` objects parsed from the payload, or an empty
        list if the structure does not match.
    """
    try:
        data = json.loads(data_str)
    except json.JSONDecodeError as e:
        logger.debug(f"Failed to parse ds:11 as JSON: {e}")
        return []

    if not isinstance(data, list) or len(data) < 3:
        return []

    message_list = data[2]
    if not isinstance(message_list, list):
        return []

    messages = []
    for entry in message_list:
        try:
            msg_data = entry[0][0]   # metadata array
            body_struct = entry[0][1] if len(entry[0]) > 1 else None

            # Sender: msg_data[2] = [[name, avatar, email, uid], ...]
            sender_arr = msg_data[2]
            first_sender = sender_arr[0] if sender_arr else []
            sender_name = first_sender[0] if len(first_sender) > 0 else ""
            sender_email = first_sender[2] if len(first_sender) > 2 else ""
            sender = f"{sender_name} <{sender_email}>" if sender_name else sender_email

            subject = msg_data[5] if len(msg_data) > 5 else ""
            timestamp = msg_data[7] if len(msg_data) > 7 else None
            date = _ts_to_rfc2822(timestamp) if isinstance(timestamp, list) else ""

            # Body: [2, [[1, [None, html_string]], ...]]
            body_html = ""
            if isinstance(body_struct, list) and len(body_struct) > 1:
                inner = body_struct[1]
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, list) and len(item) >= 2:
                            payload = item[1]
                            if isinstance(payload, list) and len(payload) >= 2:
                                candidate = payload[1]
                                if isinstance(candidate, str) and '<' in candidate:
                                    body_html = candidate
                                    break

            body = _html_to_text(body_html) if body_html else ""

            # Parse file attachments and inline images from HTML body
            attachments = []
            if body_html:
                attachments.extend(parse_attachments_from_html(body_html))
                attachments.extend(parse_inline_images_from_html(body_html))

            if sender_email:
                messages.append(Message(
                    sender=sender,
                    date=date,
                    subject=subject,
                    body=body,
                    body_html=body_html,
                    attachments=attachments,
                ))
        except Exception as e:
            logger.debug(f"Failed to parse message entry: {e}")
            continue

    return messages


class ThreadFetcher:
    """Fetch and parse all messages from a single Google Groups thread."""

    def __init__(self, page, debug_dir=None):
        """
        Args:
            page: Authenticated Playwright page.
            debug_dir: If set, the raw ``ds:11`` JSON for each thread is saved
                here as ``<thread_id>_ds11.json`` for offline inspection.
        """
        self.page = page
        self.debug_dir = debug_dir

    async def fetch_messages(self, thread_url: str) -> list[Message]:
        """
        Navigate to *thread_url* and return all messages in the thread.

        Captures network responses for the thread page URL and also reads the
        rendered page source, then extracts messages from whichever response
        contains a valid ``ds:11`` block.

        Args:
            thread_url: Fully-qualified Google Groups thread URL.

        Returns:
            List of :class:`Message` objects, or an empty list if parsing fails.
        """
        logger.info(f"Loading thread: {thread_url}")

        html_bodies = []

        async def handle_response(response):
            try:
                if response.status == 200 and "groups.google.com" in response.url and "/c/" in response.url:
                    body = await response.body()
                    html_bodies.append(body.decode("utf-8", errors="ignore"))
            except Exception as e:
                logger.debug(f"Failed to capture response: {e}")

        self.page.on("response", handle_response)

        try:
            await self.page.goto(thread_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(0.5)

            # Also grab the current page source in case the response listener missed it
            try:
                page_html = await self.page.content()
                html_bodies.append(page_html)
            except Exception:
                pass

            messages = []
            for html in html_bodies:
                m = re.search(r'AF_initDataCallback\(\{key: .ds:11., hash: .\d+., data:(.*?), sideChannel:', html, re.DOTALL)
                if m:
                    messages = _parse_ds11(m.group(1))
                    if messages:
                        break

            if not messages:
                logger.warning(f"No messages parsed from ds:11 for {thread_url}")

            logger.info(f"Extracted {len(messages)} messages from thread")

            if self.debug_dir:
                thread_id = thread_url.split("/c/")[-1]
                debug_file = self.debug_dir / f"{thread_id}_ds11.json"
                try:
                    # Save just the ds:11 block for inspection
                    for html in html_bodies:
                        m = re.search(r'AF_initDataCallback\(\{key: .ds:11., hash: .\d+., data:(.*?), sideChannel:', html, re.DOTALL)
                        if m:
                            with open(debug_file, "w", encoding="utf-8") as f:
                                f.write(m.group(1))
                            break
                except Exception as e:
                    logger.debug(f"Failed to save debug: {e}")

            return messages

        finally:
            self.page.remove_listener("response", handle_response)
