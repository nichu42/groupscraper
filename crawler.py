"""
Forum-list crawler for Google Groups.

Navigates the paginated thread index and extracts thread URLs by matching
the canonical ``/a/<domain>/g/<group>/c/<thread_id>`` pattern from the
page's rendered HTML.
"""
import asyncio
import logging
import re

logger = logging.getLogger(__name__)


async def _try_next_page(page) -> bool:
    """
    Click the *Next page* button if it exists and is not disabled.

    Google Groups renders pagination as a ``div[role="button"]`` with
    ``aria-label="Next page"``, which is present but ``aria-disabled="true"``
    on the last page.

    Returns:
        True if the click succeeded and a new page is loading, False otherwise.
    """
    try:
        btn = await page.query_selector('[aria-label="Next page"][role="button"]')
        if not btn:
            return False
        if await btn.get_attribute("aria-disabled") == "true":
            return False
        await btn.click()
        await asyncio.sleep(2)
        return True
    except Exception as e:
        logger.debug(f"Next page click failed: {e}")
        return False


async def get_all_thread_urls(page, domain: str, group: str, page_load_wait: float = 4.0) -> set[str]:
    """
    Crawl all pages of the forum index and return every thread URL found.

    Waits for ``networkidle`` then for a thread-row selector before scraping
    each page, falling back to a fixed ``page_load_wait`` sleep if the selector
    never appears (e.g. the group is empty or access is denied).  Clicks
    *Next page* repeatedly until no pagination button remains.

    Args:
        page: Authenticated Playwright page.
        domain: Google Workspace domain (e.g. ``"voltdeutschland.org"``).
        group: Group name portion of the address (e.g. ``"antragskommission"``).
        page_load_wait: Fallback sleep in seconds when the thread-row selector
            is not found within 10 s (default 4).

    Returns:
        Set of fully-qualified thread URLs.
    """
    thread_urls = set()
    thread_pattern = re.compile(
        rf'/a/{re.escape(domain)}/g/{re.escape(group)}/c/([^"\'\s&<>]+)'
    )

    forum_url = f"https://groups.google.com/a/{domain}/g/{group}"
    logger.info(f"Navigating to forum list: {forum_url}")
    await page.bring_to_front()
    await page.goto(forum_url, wait_until="domcontentloaded", timeout=60000)
    current_url = page.url
    logger.info(f"Page title: {await page.title()} | URL: {current_url}")
    if "access-error" in current_url:
        logger.error("Crawler hit access-error — session may have expired. Re-run with --reauth.")
        return thread_urls
    # Wait for thread rows to appear; fall back to a fixed delay if selector absent
    try:
        await page.wait_for_selector('[role="listitem"], [data-focus-id]', timeout=10000)
    except Exception:
        logger.debug(f"Thread row selector not found, falling back to {page_load_wait}s wait")
        await asyncio.sleep(page_load_wait)

    page_num = 1
    while True:
        html = await page.content()
        before = len(thread_urls)
        for thread_id in thread_pattern.findall(html):
            if "/m/" not in thread_id:
                thread_urls.add(
                    f"https://groups.google.com/a/{domain}/g/{group}/c/{thread_id}"
                )
        gained = len(thread_urls) - before
        logger.info(f"Page {page_num}: {len(thread_urls)} threads (+{gained})")

        clicked = await _try_next_page(page)
        if not clicked:
            logger.info("No further pagination button — reached last page")
            break

        page_num += 1

    logger.info(f"Found {len(thread_urls)} threads across {page_num} pages")
    return thread_urls
