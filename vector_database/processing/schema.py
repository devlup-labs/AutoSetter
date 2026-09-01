from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class Example(BaseModel):
    input: str
    output: str
    explanation: Optional[str] = None

class Problem(BaseModel):
    id: str
    source: str
    source_id: str
    title: str
    url: Optional[str] = None
    difficulty: Optional[str] = None
    rating: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    statement: Optional[str] = None
    input_format: Optional[str] = None
    output_format: Optional[str] = None
    constraints: Optional[str] = None
    examples: List[Example] = Field(default_factory=list)
    notes: Optional[str] = None
    solutions: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
