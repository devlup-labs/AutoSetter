from typing import List, Optional
import torch
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

from ..processing.schema import Problem

def build_embedding_text(problem: Problem) -> str:
    """
    Creates a meaningful text representation of the problem for the embedding model.
    """
    parts = []
    
    if problem.title:
        parts.append(f"Title:\n{problem.title}")
        
    if problem.difficulty:
        parts.append(f"Difficulty:\n{problem.difficulty}")
        
    if problem.tags:
        parts.append(f"Tags:\n{', '.join(problem.tags)}")
        
    if problem.statement:
        parts.append(f"Problem:\n{problem.statement}")
        
    if problem.input_format:
        parts.append(f"Input:\n{problem.input_format}")
        
    if problem.output_format:
        parts.append(f"Output:\n{problem.output_format}")
        
    if problem.constraints:
        parts.append(f"Constraints:\n{problem.constraints}")
        
    return "\n\n".join(parts)


class ProblemEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers is not installed. Please install it.")
            
        self.model_name = model_name
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print(f"Loading embedding model {model_name} on {self.device}...")
        self.model = SentenceTransformer(model_name, device=self.device)
        self.dimension = self.model.get_sentence_embedding_dimension()
        print(f"Model loaded. Vector dimension: {self.dimension}")

    def get_dimension(self) -> int:
        return self.dimension

    def embed_batch(self, problems: List[Problem]) -> List[List[float]]:
        """
        Generate embeddings for a batch of problems.
        """
        texts = [build_embedding_text(p) for p in problems]
        
        # We normalize embeddings for cosine similarity
        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        
        return embeddings.tolist()
