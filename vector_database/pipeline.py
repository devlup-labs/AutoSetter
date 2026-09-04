import os
import json
import time
from typing import Dict, List, Any
from tqdm import tqdm

from . import config
from .sources.codeforces import CodeforcesSource
from .sources.leetcode import LeetCodeSource
from .processing.normalize import normalize_problem
from .processing.deduplicate import Deduplicator
from .embeddings.embedder import ProblemEmbedder
from .qdrant.client import QdrantManager

class StateManager:
    """Manages the pipeline state for resuming."""
    def __init__(self, state_file: str):
        self.state_file = state_file
        self.state = {
            "collected": 0,
            "embedded_and_uploaded": 0,
            "processed_sources": {} # e.g. {"codeforces": 500}
        }
        self._load()
        
    def _load(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    self.state.update(json.load(f))
            except json.JSONDecodeError:
                pass
                
    def save(self):
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=4)
            
class StatsManager:
    def __init__(self, stats_file: str):
        self.stats_file = stats_file
        self.stats = {
            "total_problems": 0,
            "codeforces_count": 0,
            "leetcode_count": 0,
            "duplicates_removed": 0,
            "failed_records": 0,
            "embedding_count": 0,
            "processing_time_sec": 0
        }
        self._load()
        self.start_time = time.time()
        
    def _load(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, 'r') as f:
                    self.stats.update(json.load(f))
            except json.JSONDecodeError:
                pass
                
    def update_time(self):
        self.stats["processing_time_sec"] += (time.time() - self.start_time)
        self.start_time = time.time()
        
    def save(self):
        self.update_time()
        with open(self.stats_file, 'w') as f:
            json.dump(self.stats, f, indent=4)

class Pipeline:
    def __init__(self):
        self.state = StateManager(config.STATE_FILE)
        self.stats = StatsManager(config.STATS_FILE)
        self.dedup = Deduplicator()
        
    def collect_and_normalize(self, sources: List[str], limits: Dict[str, int]):
        """Collects problems from sources, normalizes them, and saves to final JSONL."""
        print("Starting Collection and Normalization...")
        
        final_file = config.FINAL_DIR / "problems.jsonl"
        failed_file = config.LOGS_DIR / "failed.jsonl"
        
        # If final file exists, load seen IDs into deduplicator to support resume
        if final_file.exists():
            with open(final_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            from .processing.schema import Problem
                            p = Problem(**json.loads(line))
                            self.dedup.add(p)
                        except:
                            pass
        
        for source_name in sources:
            limit = limits.get(source_name, None)
            
            # Substract already collected from limit if resuming
            collected = self.state.state["processed_sources"].get(source_name, 0)
            if limit and collected >= limit:
                print(f"Skipping {source_name}, already met limit ({collected}/{limit})")
                continue
                
            actual_limit = limit - collected if limit else None
            
            print(f"Collecting from {source_name} (target: {actual_limit})...")
            
            if source_name == 'codeforces':
                ds_path = config.RAW_DIR / "codeforces.jsonl"
                source = CodeforcesSource(dataset_path=str(ds_path) if ds_path.exists() else None)
            elif source_name == 'leetcode':
                ds_path = config.RAW_DIR / "leetcode.jsonl"
                source = LeetCodeSource(dataset_path=str(ds_path) if ds_path.exists() else None)
            else:
                continue
                
            with open(final_file, 'a', encoding='utf-8') as out_f, open(failed_file, 'a', encoding='utf-8') as err_f:
                count = 0
                for raw_prob in tqdm(source.collect(limit=actual_limit)):
                    try:
                        # Deduplicate
                        if self.dedup.is_duplicate(raw_prob):
                            self.stats.stats["duplicates_removed"] += 1
                            continue
                            
                        # Normalize
                        norm_prob = normalize_problem(raw_prob)
                        
                        # Save
                        out_f.write(norm_prob.model_dump_json() + "\n")
                        self.dedup.add(norm_prob)
                        
                        # Stats
                        self.stats.stats["total_problems"] += 1
                        if source_name == 'codeforces':
                            self.stats.stats["codeforces_count"] += 1
                        elif source_name == 'leetcode':
                            self.stats.stats["leetcode_count"] += 1
                            
                        count += 1
                        self.state.state["collected"] += 1
                    except Exception as e:
                        self.stats.stats["failed_records"] += 1
                        err_f.write(json.dumps({"source": source_name, "raw_id": raw_prob.id, "error": str(e)}) + "\n")
                        
                self.state.state["processed_sources"][source_name] = collected + count
                self.state.save()
                self.stats.save()
                
    def embed_and_upload(self):
        """Reads canonical JSON, generates embeddings, and uploads to Qdrant."""
        final_file = config.FINAL_DIR / "problems.jsonl"
        if not final_file.exists():
            print("No problems collected yet. Run collection first.")
            return
            
        print("Starting Embedding and Qdrant Upload...")
        
        embedder = ProblemEmbedder(model_name=config.EMBEDDING_MODEL)
        qdrant = QdrantManager(
            url=config.QDRANT_URL,
            collection_name=config.QDRANT_COLLECTION,
            api_key=config.QDRANT_API_KEY
        )
        
        qdrant.ensure_collection(dimension=embedder.get_dimension())
        
        from .processing.schema import Problem
        
        batch_size = config.QDRANT_BATCH_SIZE
        current_batch_probs = []
        
        skip_count = self.state.state["embedded_and_uploaded"]
        
        failed_file = config.LOGS_DIR / "upload_failed.jsonl"
        
        with open(final_file, 'r', encoding='utf-8') as f, open(failed_file, 'a', encoding='utf-8') as err_f:
            lines = f.readlines()
            
            # Resume skipping
            lines_to_process = lines[skip_count:]
            if not lines_to_process:
                print("All problems already embedded and uploaded.")
                return
                
            for line in tqdm(lines_to_process, desc="Embedding & Uploading"):
                if not line.strip():
                    continue
                    
                try:
                    prob = Problem(**json.loads(line))
                    current_batch_probs.append(prob)
                    
                    if len(current_batch_probs) >= batch_size:
                        self._process_upload_batch(embedder, qdrant, current_batch_probs, err_f)
                        current_batch_probs = []
                except Exception as e:
                    self.stats.stats["failed_records"] += 1
                    err_f.write(json.dumps({"error": str(e), "line": line}) + "\n")
                    
            # Process remaining
            if current_batch_probs:
                self._process_upload_batch(embedder, qdrant, current_batch_probs, err_f)

    def _process_upload_batch(self, embedder, qdrant, batch_probs, err_f):
        try:
            embeddings = embedder.embed_batch(batch_probs)
            qdrant.upsert_batch(batch_probs, embeddings)
            
            self.stats.stats["embedding_count"] += len(batch_probs)
            self.state.state["embedded_and_uploaded"] += len(batch_probs)
            
            self.state.save()
            self.stats.save()
        except Exception as e:
            self.stats.stats["failed_records"] += len(batch_probs)
            for p in batch_probs:
                err_f.write(json.dumps({"problem_id": p.id, "error": str(e)}) + "\n")
