"""
utils package
==============
Reusable building blocks for the AutoSetup pipeline:

- image_parser    : loads/normalizes input images (png/jpg/jpeg/pdf) into
                    base64-encoded PNG payloads that Ollama's vision models accept.
- prompt_loader   : loads prompt templates from disk and injects the {JSON}
                    placeholder (and other placeholders) safely.
- ollama_client   : thin wrapper around the official `ollama` Python library,
                    used for both vision (image+text) and text-only calls.
- json_generator  : orchestrates the Qwen-VL call that turns an image into the
                    structured problem.json specification.
- file_generator  : orchestrates the per-artifact Ollama calls that turn
                    problem.json into statement.md / solution.cpp / etc.
- sandbox_client  : compiles and executes C++ code via the sandbox server
                    (Docker + NsJail) or locally via subprocess.
- test_pipeline   : orchestrates the validate stage: compile → generate tests
                    → validate inputs → run solution → check outputs.
- packager        : bundles all generated artifacts, tests, and validation
                    report into a release-ready package directory.
"""
