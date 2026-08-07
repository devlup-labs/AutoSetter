import os
import re
import subprocess
import time
import requests
from typing import Tuple

# ==================== CONFIGURATION ====================
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen2.5-coder:7b"
CPP_COMPILER = "g++"  # Replace with 'clang++' if using Clang
CPP_FLAGS = ["-std=c++17", "-O2"]

# Feature Flags
PRINT_OPTIMAL_CODE = True  # Set to True to print the generated AC code at the end
PRINT_BRUTE_CODE = True  # Set to True to print the generated brute-force code at the end
# Filenames
SRC_AC = "solution_ac.cpp"
BIN_AC = "solution_ac.exe" if os.name == "nt" else "./solution_ac"

SRC_BRUTE = "solution_brute.cpp"
BIN_BRUTE = "solution_brute.exe" if os.name == "nt" else "./solution_brute"


# ==================== PIPELINE FUNCTIONS ====================
def query_ollama(system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "top_p": 0.9
        }
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()["message"]["content"]
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
    optimal_sys = (
        "You are an expert C++ competitive programmer writing code for an automated sandbox.\n"
        "Generate a complete, standard C++ program (C++17) that optimally solves the problem.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. REASONING FIRST: Inside a C++ multiline comment (/* ... */) at the very top of your response, write out:\n"
        "   - Careful distinction between SUBSEQUENCE (can delete arbitrary elements) and SUBARRAY (contiguous).\n"
        "   - Step-by-step logic, state definitions (e.g., Dynamic Programming / Greedy transitions), and handling of the swap operation.\n"
        "   - Edge cases (e.g., N=1, all elements equal).\n"
        "2. CODE REQUIREMENTS:\n"
        "   - Optimal time/space complexity meeting problem constraints.\n"
        "   - Standard headers (<iostream>, <vector>, <algorithm>, <map>, etc.) and fast I/O.\n"
        "   - Ensure strict typing, proper variable initialization, and correct logic.\n"
        "   - - MUST be a complete, self-contained C++ executable file.\n"
        "   - Structure strictly in standard C++ order: place all #include directives at the very top, followed by helper functions/DP structures, and strictly end with a complete int main() function.\n"
        "3. FORMATTING:\n"
        "   - Output ONLY valid executable C++ code (including the top comment block) inside standard ```cpp ``` markdown blocks."
    )
    
    brute_sys = (
        "You are an expert C++ competitive programmer writing a verification script.\n"
        "Generate a simple, naive brute-force C++ program (C++17) guaranteed to be 100% mathematically correct.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. REASONING FIRST: Inside a C++ multiline comment (/* ... */) at the top, outline the complete search space.\n"
        "2. EXHAUSTIVE SEARCH REQUIREMENT:\n"
        "   - DO NOT use greedy heuristics, shortcuts, or assumptions.\n"
        "   - Use explicit recursion, bitmasks, or brute-force loops to explore ALL possibilities (e.g., try every subsequence, test every adjacent swap, and verify valid adjacent colors).\n"
        "   - Prioritize guaranteed correctness over execution speed.\n"
        "   - MUST be a complete, self-contained C++ executable file.\n"
        "   - Structure strictly in standard C++ order: place all #include directives at the very top, followed by helper functions/DP structures, and strictly end with a complete int main() function.\n"
        "3. FORMATTING:\n"
        "   - Output ONLY valid C++ code inside standard ```cpp ``` markdown blocks."
    )

    print("[1/4] Generating Optimal Solution...")
    code_ac = extract_clean_cpp(query_ollama(optimal_sys, problem_statement))

    print("[2/4] Generating Brute-Force Solution...")
    code_brute = extract_clean_cpp(query_ollama(brute_sys, problem_statement))

    return code_ac, code_brute


# ==================== LOCAL RUNNER UTILITIES ====================
def compile_cpp(source_file: str, binary_output: str) -> Tuple[bool, str]:
    """Compiles C++ source file to a binary executable."""
    cmd = [CPP_COMPILER] + CPP_FLAGS + [source_file, "-o", binary_output]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return True, "Success"
        return False, res.stderr
    except FileNotFoundError:
        return False, f"Compiler '{CPP_COMPILER}' not found in system PATH."

def execute_binary(binary_path: str, test_input: str, timeout: float = 5.0) -> Tuple[bool, str, float]:
    """Runs binary executable with stdin and measures runtime."""
    start_time = time.perf_counter()
    try:
        res = subprocess.run(
            [binary_path],
            input=test_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        if res.returncode == 0:
            return True, res.stdout.strip(), elapsed_ms
        return False, f"Runtime Error (Exit Code {res.returncode}):\n{res.stderr}", elapsed_ms
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return False, "Time Limit Exceeded (TLE)", elapsed_ms


# ==================== MAIN TEST HARNESS ====================
def run_local_test():
    # 1. Input Problem Statement
    print("=" * 60)
    print("LOCAL PIPELINE COMPILATION & EXECUTION TEST")
    print("=" * 60)
    
    sample_problem = """
Given an integer array nums, return all the triplets [nums[i], nums[j], nums[k]] such that i != j, i != k, and j != k, and nums[i] + nums[j] + nums[k] == 0.

Notice that the solution set must not contain duplicate triplets.
Constraints:

3 <= nums.length <= 3000
-10^5 <= nums[i] <= 10^5
"""
    
    sample_test_input = """
nums = [0,1,1]
nums = [0,0,0]
nums = [-1,0,1,2,-1,-4]
"""

    # 2. Generate Code
    try:
        code_ac, code_brute = generate_cpp_solutions(sample_problem)
    except Exception as e:
        print(f"\n[!] Generation failed: {e}")
        return

    # Save generated code to disk
    with open(SRC_AC, "w", encoding="utf-8") as f:
        f.write(code_ac)
    with open(SRC_BRUTE, "w", encoding="utf-8") as f:
        f.write(code_brute)

    print("\n[3/4] Compiling generated solutions...")
    
    # 3. Compile Solutions
    ac_compiled, ac_err = compile_cpp(SRC_AC, BIN_AC)
    brute_compiled, brute_err = compile_cpp(SRC_BRUTE, BIN_BRUTE)

    if not ac_compiled:
        print(f"\n[X] Optimal C++ Compilation Failed:\n{ac_err}")
    else:
        print(" [+] Optimal C++ Compiled successfully.")

    if not brute_compiled:
        print(f"\n[X] Brute Force C++ Compilation Failed:\n{brute_err}")
    else:
        print(" [+] Brute Force C++ Compiled successfully.")

    if not (ac_compiled and brute_compiled):
        print("\n[!] Stopping execution test due to compilation failure(s).")
        return

    # 4. Execute Solutions and Compare Outputs
    print("\n[4/4] Executing test case...")
    print("--- Test Input ---")
    print(sample_test_input)
    print("------------------")

    ok_ac, out_ac, time_ac = execute_binary(BIN_AC, sample_test_input)
    ok_brute, out_brute, time_brute = execute_binary(BIN_BRUTE, sample_test_input)

    # 5. Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"Optimal Solution Output : {out_ac} ({time_ac:.2f} ms)")
    print(f"Brute Force Output     : {out_brute} ({time_brute:.2f} ms)")
    
    if ok_ac and ok_brute:
        if out_ac == out_brute:
            print("\n[PASS] BOTH OUTPUTS MATCH EXACTLY!")
        else:
            print("\n[FAIL] OUTPUT MISMATCH DETECTED!")
    else:
        print("\n[FAIL] One or both executables crashed or timed out.")

    # 6. Optional: Print Generated Code
    if PRINT_OPTIMAL_CODE:
        print("\n" + "=" * 60)
        print("OPTIMAL SOLUTION CODE (solution_ac.cpp)")
        print("=" * 60)
        print(code_ac)
        print("=" * 60)

    if PRINT_BRUTE_CODE:
        print("\n" + "=" * 60)
        print("BRUTE FORCE SOLUTION CODE (solution_brute.cpp)")
        print("=" * 60)
        print(code_brute)
        print("=" * 60)

if __name__ == "__main__":
    run_local_test()