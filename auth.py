"""
Browser-based authentication via Playwright.

Persists the full browser storage state (cookies, localStorage, sessionStorage)
to ``session.json`` so subsequent runs skip the manual login step.
Re-authenticate with ``--reauth`` when the saved session expires.
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


async def ensure_session(group_url: str, reauth: bool = False):
    """
    Return an authenticated Playwright page, launching a login flow if needed.

    If ``session.json`` exists and ``reauth`` is False, restores the saved
    storage state directly.  Otherwise opens a visible browser window and
    waits up to 15 minutes for the user to log in manually, then saves the
    resulting storage state for future runs.

    Args:
        group_url: The Google Groups URL to open during login.
        reauth: Delete any saved session and force a new login.

    Returns:
        Tuple of ``(page, context, browser, playwright)`` — all must be
        closed by the caller when scraping is complete.
    """
    storage_file = Path("session.json")

    # Reuse saved session if it exists — trust it and let the crawler fail
    # naturally if the session is truly expired (user can re-run with --reauth)
    if storage_file.exists() and not reauth:
        logger.info(f"Loading saved session from {storage_file}")
        logger.info("If session is expired, re-run with --reauth")
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(headless=False, channel="chrome")
        except Exception:
            browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=str(storage_file))
        page = await context.new_page()
        return page, context, browser, playwright

    # Delete old session if reauth requested
    if reauth and storage_file.exists():
        storage_file.unlink()
        logger.info("Deleted saved session (--reauth)")

    logger.info("")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║ AUTHENTICATION REQUIRED: Manual Login                            ║")
    logger.info("╚" + "═" * 68 + "╝")
    logger.info("")
    logger.info("Starting browser for login...")
    logger.info("")

    playwright = await async_playwright().start()
    # Use system Chrome instead of bundled Chromium (more stable on Windows)
    try:
        browser = await playwright.chromium.launch(headless=False, channel="chrome")
    except Exception as e:
        logger.warning(f"System Chrome not found, using bundled Chromium: {e}")
        browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()

    logger.info("Opening Google Groups...")
    try:
        await page.goto(group_url, timeout=30000)
    except Exception as e:
        logger.debug(f"Initial page load: {e}")

    logger.info("")
    logger.info("╔════════════════════════════════════╗")
    logger.info("║ CHROMIUM WINDOW SHOULD APPEAR      ║")
    logger.info("║                                    ║")
    logger.info("║  1. Log in with your Google account║")
    logger.info("║  2. Wait for group to load         ║")
    logger.info("║  3. *** KEEP WINDOW OPEN ***       ║")
    logger.info("║                                    ║")
    logger.info("║  Timeout: 15 minutes               ║")
    logger.info("╚════════════════════════════════════╝")
    logger.info("")

    # Wait for user to authenticate and reach the group page
    start = time.time()
    max_wait = 900  # 15 minutes
    last_logged_url = None
    crash_detected = False

    while time.time() - start < max_wait:
        try:
            current_url = page.url

            # Log URL only when it changes
            if current_url != last_logged_url:
                logger.info(f"  → URL: {current_url}")
                last_logged_url = current_url

                if "access-error" in current_url:
                    logger.info("  → Access error page. Click 'Login' and sign in with your Workspace account.")
                    logger.info("  → After login, the script will navigate to the group automatically.")

                # Check if we've reached the target group page
                if ("/g/" in current_url and "/a/" in current_url) and "access-error" not in current_url and "accounts.google.com" not in current_url:
                    logger.info("✓ Successfully authenticated!")
                    break

                # After login Google redirects to groups.google.com/?pli=1 instead of
                # following the continue= URL.  Detect any groups.google.com landing
                # that isn't the target and navigate there directly.
                if (
                    "groups.google.com" in current_url
                    and "access-error" not in current_url
                    and "accounts.google.com" not in current_url
                    and group_url.split("groups.google.com")[1] not in current_url
                ):
                    logger.info(f"  → Login complete, navigating to group URL…")
                    try:
                        await page.goto(group_url, timeout=15000)
                    except Exception:
                        pass

            # Small sleep to avoid tight loop
            await asyncio.sleep(1)

        except Exception as e:
            if not crash_detected:
                crash_detected = True
                logger.error(f"Browser connection lost: {e}")
                logger.error("The Chromium window may have closed. Please run the script again.")
            raise Exception("Browser closed or crashed during authentication.")

    # Save session for future runs
    storage_state = await context.storage_state()
    with open(storage_file, "w", encoding="utf-8") as f:
        json.dump(storage_state, f, indent=2)

    logger.info(f"✓ Session saved to {storage_file}")
    logger.info("✓ All future runs will reuse this session!")
    logger.info("")

    return page, context, browser, playwright
