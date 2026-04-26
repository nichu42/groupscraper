"""
Resumable scraping progress tracker.

Persists completed thread URLs to a JSON file so a run that is interrupted
(network error, rate limit, Ctrl-C) can resume without re-fetching already
exported threads.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ProgressTracker:
    """
    Track which threads have been fully scraped.

    State is persisted after every ``mark_done`` call so the file always
    reflects at least the threads processed so far.
    """

    def __init__(self, progress_file: Path):
        """
        Args:
            progress_file: Path to the JSON file used for persistence.
                Created on first write; silently starts empty if missing.
        """
        self.progress_file = progress_file
        self.completed_threads = self._load()

    def _load(self) -> set:
        """Load the set of completed thread URLs from disk."""
        if self.progress_file.exists():
            try:
                with open(self.progress_file) as f:
                    data = json.load(f)
                    logger.info(f"Loaded {len(data['completed'])} completed threads from {self.progress_file}")
                    return set(data["completed"])
            except Exception as e:
                logger.error(f"Failed to load progress: {e}")
                return set()
        return set()

    def mark_done(self, thread_url: str):
        """Record *thread_url* as fully scraped and persist to disk."""
        self.completed_threads.add(thread_url)
        self._save()

    def is_done(self, thread_url: str) -> bool:
        """Return True if *thread_url* has already been scraped."""
        return thread_url in self.completed_threads

    def get_pending(self, thread_urls: set) -> list:
        """
        Return the subset of *thread_urls* not yet scraped, sorted ascending.

        Args:
            thread_urls: Full set of discovered thread URLs for this group.

        Returns:
            Sorted list of URLs that still need to be processed.
        """
        pending = sorted(thread_urls - self.completed_threads)
        logger.info(f"Progress: {len(self.completed_threads)} done, {len(pending)} pending")
        return pending

    def _save(self):
        """Persist the current completed set to disk."""
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, "w") as f:
            json.dump(
                {
                    "completed": sorted(self.completed_threads),
                    "total": len(self.completed_threads)
                },
                f,
                indent=2
            )
