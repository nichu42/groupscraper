# Groups Scraper - Development Notes

## Current Architecture

The scraper uses **Playwright with system Chrome** to authenticate with Google Groups and export messages to MBOX format.

### Module Overview

- **scraper.py** — Async main orchestration. Calls: auth → crawler → thread_fetcher → mbox_writer + progress tracker
- **auth.py** — Launches headed Chrome, waits for manual Google login, saves/restores `context.storage_state()`
- **crawler.py** — Paginates the forum list by reading page HTML, clicks "Next page" button between pages
- **thread_fetcher.py** — Navigates to each thread page, extracts messages from the embedded `ds:11` data block
- **attachment_downloader.py** — Downloads attachments and inline images via the authenticated browser session
- **mbox_writer.py** — Writes RFC 2822 messages to `.mbox` file
- **progress.py** — Tracks completed threads; resumable across runs

## How It Works

### 1. Authentication (`auth.py`)

- Launches headed Chrome (`channel="chrome"`) — system Chrome is more stable than bundled Chromium on Windows
- User logs in manually; script polls `page.url` every second to detect completion
- Auth detected when URL contains `/g/` and `/a/` without `access-error` or `accounts.google.com`
- Saves `context.storage_state()` → `session.json` (captures cookies + localStorage + sessionStorage)
- Subsequent runs load `session.json` and verify the session is still valid before proceeding

### 2. Thread Enumeration (`crawler.py`)

- Navigates to the group forum list URL
- Reads `page.content()` HTML and extracts thread IDs via regex (`/c/{thread_id}`)
- Clicks the `div[aria-label="Next page"][role="button"]` button to advance pages
- Repeats until no "Next page" button is found
- **Note:** Uses `page.content()` (not network interception) because async response listeners were causing browser crashes during page navigation on Windows

### 3. Message Extraction (`thread_fetcher.py`)

- Navigates to each thread URL
- Listens for network responses from `groups.google.com` URLs containing `/c/`
- Also reads `page.content()` as fallback
- Searches HTML for `AF_initDataCallback({key: 'ds:11', ...})` block
- Parses the `ds:11` JSON structure to extract all messages

#### ds:11 Data Structure (confirmed by live inspection)

```
data[0]           = [group_id, group_email]
data[1]           = thread summary
data[2]           = list of message entries
data[2][i][0][0]  = metadata: [group_id, msg_id, sender_array, 0, None,
                               subject, body_preview, [ts_secs, ts_nanos], ...]
data[2][i][0][1]  = body: [2, [[1, [None, "<html body>"]]]]

sender_array = [
  [name, avatar_url, email, user_id],       ← index 0: From sender
  [[name, avatar_url, email, user_id], ...]  ← index 1: To recipients
]
```

`data[0][1]` is the group email and is used as the `To:` fallback when `sender_array[1]` is empty.
`msg_id` (string, e.g. `"imKKdxiRBAAJ"`) matches the `data-message-id` attribute in the rendered DOM and is used to correlate page-rendered attachments with their message.

#### Attachment parsing

Google Groups renders the attachment section (`div.c2eF9b`) outside the email body HTML stored in `ds:11`. `parse_attachments_from_html(body_html)` therefore only catches inline images embedded in the body. A second pass, `parse_page_attachments_by_msgid(page_html)`, scans the full rendered `page.content()` HTML, associates each `data-view-attachment-url` element with the nearest preceding `data-message-id`, and merges any missing attachments into the correct `Message` object.

### 4. Output

- Messages are serialised with `email.generator.BytesGenerator` and appended directly to the `.mbox` file via `open("ab")` (RFC 4155 format, compatible with Thunderbird/Apple Mail). The `mailbox.mbox` stdlib class is intentionally avoided — it calls `file.truncate()` on close, which raises `io.UnsupportedOperation` on certain message structures in Python 3.14 on Windows.
- `progress.json` marks each thread URL as done after its messages are written
- Re-running the script skips already-completed threads

## CLI

```bash
# First run — opens Chrome for Google login
python scraper.py mygroup@example.com

# Subsequent runs — reuses saved session
python scraper.py mygroup@example.com

# Force re-authentication
python scraper.py mygroup@example.com --reauth

# Debug mode — saves ds:11 JSON blocks for inspection
python scraper.py mygroup@example.com --debug

# Process only first N pending threads (useful for testing)
python scraper.py mygroup@example.com --limit 5
```

> On a successful complete run (all threads processed), the `attachments/` folder and `progress.json` are automatically removed — only the `.mbox` file remains.

## Known Risks

| Risk | Mitigation |
|------|-----------|
| Async response listeners crash browser during page navigation | crawler.py uses `page.content()` instead of response interception |
| `page.content()` occasionally crashes on Windows | Wrapped in try/except; falls back gracefully |
| Session expiry mid-run | Auto-detected via `SessionExpiredError`; script re-authenticates and continues |
| Rate limiting | Fixed 0.5s delay per thread; increase if needed |
| Long threads with 50+ replies may be paginated | Not yet handled; ds:11 may only contain first batch |

## Architecture Decision Log

**Selenium → Playwright (2026-04-24)**
- Selenium had no usable network interception; cookies alone were insufficient for Google Groups auth
- Playwright's `context.storage_state()` captures full session state

**Network interception → `page.content()` for forum list crawler (2026-04-25)**
- Async response listeners caused browser crashes on Windows when the forum list page navigated between pages
- `page.content()` reads the current DOM directly; simpler and stable

**`networkidle` → `domcontentloaded` wait strategy (2026-04-25)**
- `networkidle` was ~30s per thread
- `domcontentloaded` + 0.5s sleep is ~1.5s per thread; ds:11 block is already in the initial HTML

**`mailbox.mbox` → direct `BytesGenerator` + `open("ab")` (2026-04-27)**
- `mailbox.mbox` calls `file.truncate()` on close when internal state is modified; raises `io.UnsupportedOperation` on certain message structures in Python 3.14 on Windows
- Direct append avoids all file-rewriting and truncation

**Attachment parsing extended to full page HTML (2026-04-27)**
- `body_html` from ds:11 contains only the email body; Google Groups renders the attachment strip (`div.c2eF9b`) outside the body via JavaScript
- `parse_page_attachments_by_msgid()` scans `page.content()` and matches attachments to messages via `data-message-id`

## Outstanding Work

- **Long thread pagination** — if a thread has 50+ messages, ds:11 may only contain the first batch; needs investigation
