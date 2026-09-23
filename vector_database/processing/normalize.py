import re
import html
from .schema import Problem

def normalize_whitespace(text: str) -> str:
    """Normalize whitespace while preserving newlines."""
    if not text:
        return ""
    # Replace multiple spaces with a single space
    text = re.sub(r'[ \t]+', ' ', text)
    # Replace 3 or more newlines with double newline
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def strip_unnecessary_html(text: str) -> str:
    """
    Basic HTML stripping that attempts to preserve code and math.
    Note: For a production system, a more robust parser (like BeautifulSoup) is recommended.
    """
    if not text:
        return ""
        
    # Unescape HTML entities (e.g. &lt; to <, &quot; to ")
    text = html.unescape(text)
    
    # Very basic tag removal, ignoring <pre> and <code> blocks if we want a simple approach.
    # For now, we will just remove common layout tags and keep text.
    # In competitive programming, math is often in $...$ or \( ... \)
    
    # Remove script and style tags completely along with their content
    text = re.sub(r'<(script|style).*?>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Replace <br> and <p> with newlines
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
    
    # Strip remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    return text

def normalize_problem(problem: Problem) -> Problem:
    """
    Normalizes a problem's text fields without destroying mathematical notation or code.
    """
    # Normalize title
    if problem.title:
        problem.title = normalize_whitespace(problem.title)
        
    # Normalize text fields
    if problem.statement:
        problem.statement = normalize_whitespace(strip_unnecessary_html(problem.statement))
        
    if problem.input_format:
        problem.input_format = normalize_whitespace(strip_unnecessary_html(problem.input_format))
        
    if problem.output_format:
        problem.output_format = normalize_whitespace(strip_unnecessary_html(problem.output_format))
        
    if problem.constraints:
        problem.constraints = normalize_whitespace(strip_unnecessary_html(problem.constraints))
        
    if problem.notes:
        problem.notes = normalize_whitespace(strip_unnecessary_html(problem.notes))
        
    # Normalize examples
    for ex in problem.examples:
        if ex.input:
            ex.input = ex.input.strip()
        if ex.output:
            ex.output = ex.output.strip()
        if ex.explanation:
            ex.explanation = normalize_whitespace(strip_unnecessary_html(ex.explanation))
            
    # Normalize tags (lowercase, stripped)
    problem.tags = [t.lower().strip() for t in problem.tags if t.strip()]
    # Deduplicate tags
    problem.tags = list(dict.fromkeys(problem.tags))
    
    return problem
