import json
from typing import Iterator
from pathlib import Path
from .base import BaseSource
from ..processing.schema import Problem

class LeetCodeSource(BaseSource):
    """
    LeetCode source collector.
    Since LeetCode has no official open REST API for full statements, 
    this relies on a local JSONL dataset by default.
    """
    def __init__(self, dataset_path: str = None):
        self.dataset_path = dataset_path

    def collect(self, limit: int = None) -> Iterator[Problem]:
        if not self.dataset_path or not Path(self.dataset_path).exists():
            print(f"Warning: LeetCode dataset not found at {self.dataset_path}. Skipping LeetCode.")
            return

        count = 0
        with open(self.dataset_path, 'r', encoding='utf-8') as f:
            for line in f:
                if limit and count >= limit:
                    break
                if not line.strip():
                    continue
                data = json.loads(line)
                
                source_id = str(data.get('id', data.get('source_id', '')))
                
                yield Problem(
                    id=f"leetcode_{source_id.lower().replace(' ', '_')}",
                    source="leetcode",
                    source_id=source_id,
                    title=data.get('title', ''),
                    url=data.get('url'),
                    difficulty=str(data.get('difficulty', '')),
                    rating=None,  # LC generally uses Easy/Medium/Hard instead of numerical rating
                    tags=data.get('tags', []),
                    statement=data.get('statement'),
                    input_format=data.get('input_format'),
                    output_format=data.get('output_format'),
                    constraints=data.get('constraints'),
                    examples=data.get('examples', [])
                )
                count += 1
