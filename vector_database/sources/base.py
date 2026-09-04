from abc import ABC, abstractmethod
from typing import Iterator
from ..processing.schema import Problem

class BaseSource(ABC):
    """Base class for all problem sources."""
    
    @abstractmethod
    def collect(self, limit: int = None) -> Iterator[Problem]:
        """
        Collect problems from the source.
        
        Args:
            limit (int): Maximum number of problems to collect.
            
        Yields:
            Problem: Un-normalized Canonical Problem object.
        """
        pass
