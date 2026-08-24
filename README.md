# AutoSetter

AutoSetter turns competitive programming problem images or PDFs into verified Codeforces/Polygon-style problem packages: specification (`problem.json`), Markdown statement, reference solution, testlib validator, generator, checker, test cases, and a validation report — driven by local Qwen models through Ollama and isolated sandboxed execution.

```text
statement image / PDF
         │  Qwen-VL
         ▼
    problem.json  ──►  statement.md, solution.cpp, validator.cpp,
         │             generator.cpp, checker.cpp     (one Ollama call each)
         ▼
      validate  (compile, generate, validate, solve, check, probe)
         ▼
      package/  (release bundle ready for human review & Polygon)
```

---

## Quickstart (Google Colab + Cloudflare Tunnel)

### Step 1: Run this in a Google Colab notebook (GPU runtime)

```python
# 1. Install Ollama & Cloudflared
!apt-get update -qq && apt-get install -y -qq zstd pciutils lshw
!curl -fsSL https://ollama.com/install.sh | sh
!wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
!dpkg -i cloudflared-linux-amd64.deb

# 2. Start Ollama in the background
import os
import time
os.environ['OLLAMA_HOST'] = '0.0.0.0'
get_ipython().system_raw('ollama serve > ollama.log 2>&1 &')
time.sleep(5)

# 3. Pull the correct 7B models
!ollama pull qwen2.5vl:7b
!ollama pull qwen2.5-coder:7b

# 4. Start Cloudflare tunnel and print the URL
get_ipython().system_raw('cloudflared tunnel --url http://localhost:11434 > tunnel.log 2>&1 &')
time.sleep(8)
print("\n--- COPY THE URL BELOW ---")
!grep -o 'https://.*\.trycloudflare\.com' tunnel.log
print("--------------------------\n")
```

### Step 2: Run this on your local terminal

```bash
# 1. Clean previous run outputs
rm -rf out

# 2. Set environment variables (Replace the URL with the one Colab printed)
export OLLAMA_HOST="https://YOUR-NEW-URL.trycloudflare.com"
export AUTOSETTER_VISION_MODEL="qwen2.5vl:7b"
export AUTOSETTER_TEXT_MODEL="qwen2.5-coder:7b"

# 3. Execute the pipeline
python3 app.py statement.png
```

## Clean 4-Folder Repository Structure

The repository is organized into **exactly 4 top-level directories**, keeping the root clean, modular, and intuitive:

```text
AutoSetter/
├── ARCHITECTURE.md         # Full architecture and design documentation
├── README.md               # Quickstart and overview
├── app.py                  # Top-level CLI entry point
├── autosetter/             # 1. CORE PACKAGE & ENGINE
│   ├── config.py           # Centralized configuration & environment variables
│   ├── llm.py              # Ollama API client (multimodal vision + text)
│   ├── vision.py           # Image & PDF ingestion/normalization (Pillow, PyMuPDF)
│   ├── prompts.py          # Prompt loader & safe placeholder substitution
│   ├── extractor.py        # Vision extraction -> validated problem.json
│   ├── generator.py        # Code generation for statement & testlib C++
│   ├── sandbox.py          # C++ compilation & sandbox execution (local + HTTP)
│   ├── pipeline.py         # Validation engine (sample verification & checker probes)
│   ├── packager.py         # Release package assembly & manifest generation
│   ├── polygon.py          # Codeforces Polygon API v2 client & publisher
│   ├── cli.py              # Command-line interface & pipeline runner
│   ├── include/            # Vendored C++ headers (testlib.h)
│   │   └── testlib.h
│   └── prompts/            # Generation prompt templates
│       ├── checker.txt
│       ├── generator.txt
│       ├── json_extraction.txt
│       ├── solution.txt
│       ├── statement.txt
│       └── validator.txt
├── sandbox/                # 2. SANDBOX INFRASTRUCTURE
│   ├── docker/             # Worker container definition (Dockerfile)
│   ├── scripts/            # Lifecycle scripts (build.sh, start.sh, stop.sh)
│   └── server/             # Express container pool & NsJail execution server
├── tests/                  # 3. TEST SUITE
│   ├── conftest.py         # Shared fixtures (StubOllamaClient)
│   ├── fixtures.py         # Miniature C++ test problem fixtures
│   ├── test_cli.py         # CLI parser & orchestration tests
│   ├── test_extractor.py   # JSON extraction & schema validation tests
│   ├── test_generator.py   # Code generation & code fence stripping tests
│   ├── test_packager.py    # Release package & manifest verification tests
│   ├── test_pipeline.py    # Sandboxed validation & checker probe tests
│   ├── test_sandbox.py     # Local g++ compilation & execution tests
│   └── test_vision.py      # Image & PDF parsing tests
└── example/                # 4. EXAMPLES & DEMO
    ├── 01_intake/          # Problem screenshot + problem.json
    ├── 02_build/           # Generated statement & C++ sources
    ├── 03_validate/        # Validation report & test cases
    ├── 04_package/         # Packaged bundle ready for release
    ├── demo/               # Interactive web pipeline demo
    ├── run_mock_pipeline.py# Offline mock pipeline execution script
    └── README.md           # Example walkthrough documentation
```

---

## Setup & Installation

### Requirements
- Python 3.10+
- `g++` on PATH (supporting C++17)
- Local [Ollama](https://ollama.com/) instance with models pulled:
  ```bash
  ollama pull qwen2.5vl:3b
  ollama pull qwen2.5-coder:7b
  ```

### Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Usage

### CLI Execution
Run directly on an image or PDF:
```bash
python app.py path/to/statement.png
```

Or via module invocation:
```bash
python -m autosetter statement.pdf --num-tests 20
```

### CLI Options
```text
positional arguments:
  image_path              Path to statement image (.png/.jpg) or PDF

options:
  --vision-model MODEL    Vision model name (default: qwen2.5vl:3b)
  --text-model MODEL      Text model name (default: qwen2.5-coder:7b)
  --host URL              Ollama server URL (default: http://localhost:11434)
  --num-tests N           Number of test cases to generate (default: 10)
  --skip-validation       Skip sandbox compilation and validation stage
  --out-dir DIR           Output directory (default: out/)
```

### Output Layout
All outputs are created in `out/` (which is excluded in `.gitignore`):
```text
out/
  problem.json          # Structured problem specification
  generated/            # statement.md and C++ source files
  tests/                # 001.in / 001.ans pairs, plus rejected/ for invalid inputs
  package/              # Assembled bundle with manifest.json
```

### Exit Codes
| Code | Meaning |
|---|---|
| `0` | Package is verified and **fit to release** |
| `1` | Pipeline failed |
| `2` | Artifacts were generated, but package is **not fit to release** (validation failed) |

Set `AUTOSETTER_DEBUG=1` in your environment to view raw model outputs.

---

## How the Pipeline Prevents False Positives

When all C++ files are generated by a single model from the same JSON, they can share identical mistakes. AutoSetter enforces three independent verification layers:

1. **Official Samples Verify the Validator**:
   The statement's official samples are known-good ground truth. A correct validator must accept all of them. If it rejects them, the validator or extracted constraints are flagged.
2. **The Validator Verifies the Generator**:
   Every generated test is validated. If the validator accepts official samples but rejects a generated test, the **generator** is diagnosed as the faulty file.
3. **Flawed Outputs Probe the Checker**:
   Running a checker on the reference solution only checks whether $x == x$. AutoSetter feeds the checker corrupted outputs (empty file, truncated answer, perturbed numeric values). A checker that accepts wrong outputs is flagged as untrusted.

---

## Testing

```bash
pip install -r requirements.txt
pytest
```

The test suite stubs the Ollama interface, so no running AI models are needed to test parsing, generation, validation, packaging, and CLI flows.

---

## Sandbox (Docker + NsJail)

For production isolation of untrusted code, `sandbox/` provides a Docker + NsJail worker pool:

```bash
bash sandbox/scripts/build.sh    # Builds autosetter-nsjail Docker image
bash sandbox/scripts/start.sh    # Starts Express pool manager on port 3000
bash sandbox/scripts/stop.sh     # Shuts down workers
```

Configuration and limits are defined in [`sandbox/server/src/config.js`](file:///Users/shreshthdhimole/AutoSetter/sandbox/server/src/config.js).

---

## Polygon Integration

AutoSetter includes automated publishing to Codeforces Polygon:
```bash
export POLYGON_API_KEY="your-api-key"
export POLYGON_SECRET="your-secret"

python -m autosetter.polygon out/package 123456
```

---

## Documentation

For full system details, read [`ARCHITECTURE.md`](file:///Users/shreshthdhimole/AutoSetter/ARCHITECTURE.md).
