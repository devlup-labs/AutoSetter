import json
import requests
import time
from typing import Iterator
from pathlib import Path
from .base import BaseSource
from ..processing.schema import Problem

class CodeforcesSource(BaseSource):
    """
    Codeforces source collector.
    Supports both official API (metadata only) and local JSONL datasets (for full statements).
    """
    def __init__(self, dataset_path: str = None):
        self.dataset_path = dataset_path

    def collect(self, limit: int = None) -> Iterator[Problem]:
        if self.dataset_path and Path(self.dataset_path).exists():
            yield from self._collect_from_dataset(limit)
        else:
            yield from self._collect_from_api(limit)

    def _collect_from_dataset(self, limit: int = None) -> Iterator[Problem]:
        count = 0
        with open(self.dataset_path, 'r', encoding='utf-8') as f:
            for line in f:
                if limit and count >= limit:
                    break
                if not line.strip():
                    continue
                data = json.loads(line)
                
                # Assume dataset provides these fields. Adjust based on actual dataset structure.
                yield Problem(
                    id=f"cf_{data.get('id', data.get('source_id'))}",
                    source="codeforces",
                    source_id=str(data.get('id', data.get('source_id', ''))),
                    title=data.get('title', ''),
                    url=data.get('url'),
                    difficulty=str(data.get('rating', data.get('difficulty'))),
                    rating=data.get('rating'),
                    tags=data.get('tags', []),
                    statement=data.get('statement', data.get('description')),
                    input_format=data.get('input_format'),
                    output_format=data.get('output_format'),
                    constraints=data.get('constraints'),
                   examples=data.get('examples') if data.get('examples') is not None else []

                )
                count += 1

    def _collect_from_api(self, limit: int = None) -> Iterator[Problem]:
        url = "https://codeforces.com/api/problemset.problems"
        response = requests.get(url)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch from Codeforces API: {response.status_code}")
            
        data = response.json()
        if data['status'] != 'OK':
            raise Exception("Codeforces API returned non-OK status")
            
        problems = data['result']['problems']
        
        count = 0
        for p in problems:
            if limit and count >= limit:
                break
                
            contest_id = str(p.get('contestId', ''))
            index = str(p.get('index', ''))
            source_id = f"{contest_id}{index}"
            
            # API doesn't provide problem statement
            yield Problem(
                id=f"cf_{source_id}",
                source="codeforces",
                source_id=source_id,
                title=p.get('name', ''),
                url=f"https://codeforces.com/problemset/problem/{contest_id}/{index}" if contest_id else None,
                difficulty=str(p.get('rating')) if p.get('rating') else None,
                rating=p.get('rating'),
                tags=p.get('tags', []),
                statement=None,  # Not available via official API
                input_format=None,
                output_format=None,
                constraints=None,
                examples=[]
            )
            count += 1
            # Be polite to API
            time.sleep(0.1)
