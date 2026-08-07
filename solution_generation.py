import requests
import json
import re
from typing import Dict, Tuple

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:7b" # Adjust to match your exact Ollama tag name

def query_ollama(system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,  # Low temperature keeps output deterministic
            "top_p": 0.9
        }
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"]
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to communicate with Ollama server: {e}")

def extract_clean_cpp(raw_text: str) -> str:
    pattern = r"```(?:cpp|c\+\+)?\s*\n?(.*?)\n?\s*```"
    matches = re.findall(pattern, raw_text, re.DOTALL | re.IGNORECASE)
    code = max(matches, key=len).strip() if matches else raw_text.strip()
    
    first_cpp_line = re.search(r'(#include|using namespace|/\*|//|int\s+main)', code)
    if first_cpp_line:
        code = code[first_cpp_line.start():]
        
    return code.strip()

def generate_cpp_solutions(problem_statement: str) -> Tuple[str, str]:
    """
    Takes a cleaned problem statement string from your pipeline
    and outputs (solution_ac_cpp, solution_brute_cpp).
    """
    
    # Prompts
    optimal_sys = (
        "You are an expert C++ competitive programmer writing code for an automated sandbox.\n"
        "Generate a complete, standard C++ program (C++17) that optimally solves the problem.\n"
        "Requirements:\n"
        "1. Optimal time/space complexity based on problem constraints.\n"
        "2. Standard headers (<iostream>, <vector>, <algorithm>, etc.) and std::cin/cout fast I/O.\n"
        "3. Output ONLY the raw code inside standard ```cpp ``` markdown blocks. No explanations."
    )
    
    brute_sys = (
        "You are an expert C++ competitive programmer writing code for an automated sandbox.\n"
        "Generate a simple, naive brute-force C++ program (C++17) guaranteed to be conceptually correct.\n"
        "Requirements:\n"
        "1. Naive logic (e.g., O(N^2) or recursion O(2^N)). Focus strictly on correctness, not efficiency.\n"
        "2. Standard headers and complete main() function.\n"
        "3. Output ONLY the raw code inside standard ```cpp ``` markdown blocks. No explanations."
    )

    # 1. Generate Optimal Solution
    print("[+] Generating Optimal Solution...")
    raw_ac = query_ollama(optimal_sys, problem_statement)
    code_ac = extract_clean_cpp(raw_ac)

    # 2. Generate Brute Force Solution
    print("[+] Generating Brute-Force Solution...")
    raw_brute = query_ollama(brute_sys, problem_statement)
    code_brute = extract_clean_cpp(raw_brute)

    return code_ac, code_brute

# Example integration into your main application
if __name__ == "__main__":
    # Simulated input coming from another part of your pipeline
    sample_problem = """
    Title: Maximum Subarray Sum
    Constraints: N <= 200,000; -10^9 <= A[i] <= 10^9
    Input Format: First line contains N. Second line contains N space-separated integers.
    Output Format: Print a single integer representing the maximum subarray sum.
    """

    code_ac, code_brute = generate_cpp_solutions(sample_problem)

    # Save to disk for compilation inside sandbox (e.g. g++ -O2 solution_ac.cpp -o solution_ac)
    with open("solution_ac.cpp", "w", encoding="utf-8") as f:
        f.write(code_ac)

    with open("solution_brute.cpp", "w", encoding="utf-8") as f:
        f.write(code_brute)

    print("\nFiles successfully created:")
    print(" - solution_ac.cpp")
    print(" - solution_brute.cpp")