# Group Scraper

Export all messages from a private Google Group to MBOX format (compatible with Thunderbird, Apple Mail, etc.).

Google Groups has no native export for message content. GYB and the Admin Export Tool only work with user mailboxes, not groups. Google Vault requires a licence. The official API covers settings and membership only. This tool uses browser automation to export everything.

## Download

[**Download the latest version as a ZIP**](https://codeberg.org/nichu42/groupscraper/archive/main.zip), then unzip it to a folder of your choice.

## Installation

This tool requires **Python 3.10 or newer**. If you have never used Python before, follow the steps for your operating system below.

### Step 1 — Check whether Python is installed

Open a terminal (see Step 2 for how to do that) and run:

```
python --version
```

If you see something like `Python 3.11.2`, you are good. If you get an error or a version below 3.10, install Python first.

### Step 2 — Install Python (if needed)

**Windows and macOS:**
Go to [python.org/downloads](https://www.python.org/downloads/) and download the latest installer.

- **Windows:** Run the installer. On the first screen, check the box **"Add Python to PATH"** before clicking Install. This is easy to miss and essential.
- **macOS:** Run the installer and follow the steps.

**Linux:**
Python 3 is usually pre-installed. If not:

```bash
# Debian / Ubuntu
sudo apt install python3 python3-pip

# Fedora
sudo dnf install python3 python3-pip
```

### Step 3 — Open a terminal in the project folder

You need a terminal (command line window) opened inside the folder you unzipped.

**Windows:**
1. Open File Explorer and navigate into the unzipped folder.
2. Click the address bar at the top so it becomes editable, type `cmd`, and press Enter.
3. A Command Prompt window opens, already in the right folder.

**macOS:**
1. Open Terminal (find it via Spotlight: press `Cmd + Space`, type `Terminal`, press Enter).
2. Type `cd ` (with a space after it), then drag the unzipped folder from Finder into the Terminal window, and press Enter.

**Linux:**
Open your terminal emulator and navigate to the folder:

```bash
cd /path/to/groupscraper-main
```

### Step 4 — Install dependencies

With the terminal open in the project folder, run these two commands one after the other.

**Windows:**
```
python -m pip install -r requirements.txt
python -m playwright install chromium
```

**macOS / Linux:**
```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

The first command installs the required Python packages. The second downloads a browser that the tool uses to log in to Google — you do not need Google Chrome installed separately.

That's it — the tool is ready to use.

## Usage

### First run — manual login required

```bash
python scraper.py mygroup@example.com
```

> **macOS / Linux:** Use `python3` instead of `python` if the above doesn't work.

A browser window will open. Log in with your Google account and wait for the group page to load. The script detects the successful login automatically and saves the session. The window stays open during scraping.

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

## Terms of Service

Using this tool could probably violate Google's Terms of Service. However, this tool operates as a slow, almost human-paced browser session using your own credentials, not an API scraper hitting Google at scale.

**EU users:** GDPR's right to data portability (Art. 20) enshrines the right to receive your personal data in a portable format. Google provides no native export for Google Groups message content, making this tool the only practical way to exercise that right. This does not authorise the use of automated means under Google's ToS, but it does reinforce the legitimacy of the underlying goal.

Use at your own discretion.

## Disclaimer

This tool is an independent, open-source project and is not affiliated with, endorsed by, or in any way connected to Google LLC. "Google Groups" is a trademark of Google LLC. Use of that name here is purely descriptive - this tool targets the Google Groups platform but is neither authorized nor supported by Google.

This software is provided **as is, without warranty of any kind**. The author accepts no liability whatsoever for any damages, data loss, account suspension, legal consequences, or any other harm arising from the use or misuse of this tool. Use it entirely at your own risk.

## License

This project is licensed under **GPL v3 or later**. See the [LICENSE](LICENSE) file for details.
