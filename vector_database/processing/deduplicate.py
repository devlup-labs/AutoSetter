import re
from typing import Set, Tuple
from .schema import Problem

class Deduplicator:
    def __init__(self):
        self.seen_ids: Set[str] = set()
        self.seen_urls: Set[str] = set()
        self.seen_titles: Set[str] = set()

    def _normalize_title_for_dedup(self, title: str) -> str:
        """Create a simplified title for duplicate detection."""
        if not title:
            return ""
        # Lowercase and remove all non-alphanumeric characters
        t = title.lower()
        t = re.sub(r'[^a-z0-9]', '', t)
        return t

    def is_duplicate(self, problem: Problem) -> bool:
        """
        Check if a problem is a duplicate based on multiple signals.
        Returns True if it's a duplicate, False otherwise.
        """
        # 1. Stable Unique ID Check
        if problem.id in self.seen_ids:
            return True
            
        # 2. Canonical URL Check
        if problem.url and problem.url in self.seen_urls:
            return True
            
        # 3. Normalized Title Check
        norm_title = self._normalize_title_for_dedup(problem.title)
        if norm_title and norm_title in self.seen_titles:
            return True
            
        return False

    def add(self, problem: Problem):
        """Register a problem as seen."""
        self.seen_ids.add(problem.id)
        if problem.url:
            self.seen_urls.add(problem.url)
            
        norm_title = self._normalize_title_for_dedup(problem.title)
        if norm_title:
            self.seen_titles.add(norm_title)
