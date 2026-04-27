# Group Scraper

Export all messages from a private Google Group to MBOX format (compatible with Thunderbird, Apple Mail, etc.).

**Disclaimer:** This tool is an independent, open-source project and is not affiliated with, endorsed by, or in any way connected to Google LLC. "Google Groups" is a trademark of Google LLC. Use of that name here is purely descriptive - this tool targets the Google Groups platform but is neither authorized nor supported by Google.

This software is provided **as is, without warranty of any kind**. The author accepts no liability whatsoever for any damages, data loss, account suspension, legal consequences, or any other harm arising from the use or misuse of this tool. Use it entirely at your own risk.

## Terms of Service

Using this tool could probably violate Google's Terms of Service. However,  this tool operates as a slow, almost human-paced browser session using your own credentials, not an API scraper hitting Google at scale.

**EU users:** GDPR's right to data portability (Art. 20) enshrines the right to receive your personal data in a portable format. Google provides no native export for Google Groups message content, making this tool the only practical way to exercise that right. This does not authorise the use of automated means under Google's ToS, but it does reinforce the legitimacy of the underlying goal.

Use at your own discretion.

## Why This Exists

Google Groups has no native export for message content. GYB and the Admin Export Tool only work with user mailboxes, not groups. Google Vault requires a licence. The official API covers settings and membership only. This tool uses browser automation to export everything.

## Installation

```bash
pip install -r requirements.txt
playwright install chromium
```

Requires Python 3.10+ and Google Chrome installed on the system (Windows, macOS, Linux).

## Usage

### First run — manual login required

```bash
python scraper.py mygroup@example.com
```

A Chrome window will open. Log in with your Google account and wait for the group page to load. The script detects the successful login automatically and saves the session. The window stays open during scraping.

### Subsequent runs — fully automatic

```bash
python scraper.py mygroup@example.com
```

The saved session is reused. If it has expired, the script detects this automatically and opens a browser window for re-authentication before continuing.

### All options

```
GROUP_EMAIL            Group email address (e.g. mygroup@example.com)
--reauth               Force re-authentication (delete and renew saved session)
--debug                Save raw ds:11 JSON blocks to debug/ for inspection
--limit N              Process only the first N pending threads (useful for testing)
--attachments          Download and embed attachments in MBOX
--no-attachments       Skip attachments (no prompt)
--page-load-wait SECS  Fallback wait time (seconds) if the thread list does not
                       appear within 10 s; increase on slow connections (default: 4)
```

If the group email is omitted, you will be prompted interactively. Attachments are prompted unless `--attachments` or `--no-attachments` is specified.

## Output

```
exports/
└── {domain}__{group}/
    └── {YYYY-MM-DD}/
        ├── messages.mbox     ← import this into your mail client
        ├── attachments/      ← downloaded attachment files (--attachments only)
        ├── progress.json     ← tracks completed threads for resumability
        └── debug/            ← raw data blocks (--debug only)
```

Import `messages.mbox` into Thunderbird via **Tools → Import**, or into Apple Mail via **File → Import Mailboxes**.

## Resumability

If the scraper is interrupted, re-run the same command. It reads `progress.json` and skips threads that were already fully exported.

## Known Limitations

- **Very long threads** (50+ replies) may be truncated if Google paginates the thread data — the `ds:11` block may only contain the first batch of messages.

## Troubleshooting

**Session expired mid-run**
The script detects session expiry automatically and re-authenticates without needing a restart. Use `--reauth` only to force a fresh login manually.

**0 threads found**
Verify you can access the group in your browser. Then:
```bash
python scraper.py mygroup@example.com --reauth
```

**Wrong message content or parsing errors**
Run with `--debug` and inspect the `.json` files in `debug/` to see the raw data Google returned.

## License

This project is licensed under **GPL v3 or later**. See the [LICENSE](LICENSE) file for details.