"""
Unit tests for autosetter.cli (CLI parser and end-to-end orchestration).
"""

from __future__ import annotations

import json
from pathlib import Path
# pyrefly: ignore [missing-import]
import pytest
from PIL import Image

from autosetter.cli import (
    AutoSetterError,
    build_arg_parser,
    generate_from_image,
    main,
)

SAMPLE_PROBLEM = {
    "title": "A Plus B",
    "story": "Given a and b, compute a + b.",
    "input_format": "Two integers a and b.",
    "output_format": "Single integer sum.",
    "constraints": "1 <= a, b <= 100",
    "samples": [{"input": "2 3\n", "output": "5\n", "explanation": ""}],
    "time_limit": "1s",
    "memory_limit": "256MB",
    "notes": "",
}


def test_build_arg_parser_defaults():
    parser = build_arg_parser()
    args = parser.parse_args(["problem.png"])
    assert args.image_path == "problem.png"
    assert args.num_tests == 10
    assert args.skip_validation is False


def test_build_arg_parser_overrides():
    parser = build_arg_parser()
    args = parser.parse_args([
        "statement.pdf",
        "--vision-model", "qwen2.5vl:7b",
        "--text-model", "qwen2.5-coder:7b",
        "--num-tests", "20",
        "--skip-validation",
        "--out-dir", "custom_out",
    ])
    assert args.image_path == "statement.pdf"
    assert args.vision_model == "qwen2.5vl:7b"
    assert args.text_model == "qwen2.5-coder:7b"
    assert args.num_tests == 20
    assert args.skip_validation is True
    assert args.out_dir == "custom_out"


def test_generate_from_image_missing_file_raises_error(tmp_path: Path):
    missing = tmp_path / "missing.png"
    with pytest.raises(AutoSetterError) as excinfo:
        generate_from_image(missing)
    assert "Input file not found" in str(excinfo.value)


def test_generate_from_image_end_to_end(tmp_path: Path, monkeypatch, stub_client):
    img_path = tmp_path / "problem.png"
    img = Image.new("RGB", (50, 50), color="white")
    img.save(img_path, format="PNG")

    out_dir = tmp_path / "out"

    # Stub Ollama responses
    canned_replies = {
        "OCR specialist": "```json\n" + json.dumps(SAMPLE_PROBLEM) + "\n```",
    }
    client = stub_client(
        replies=canned_replies,
        default="```cpp\n#include <iostream>\nint main() { return 0; }\n```",
    )
    monkeypatch.setattr("autosetter.cli.OllamaClient", lambda **kwargs: client)

    result = generate_from_image(
        image_path=img_path,
        out_dir=out_dir,
        skip_validation=True,
    )

    assert result.generated_dir == out_dir / "generated"
    assert result.package_dir == out_dir / "package"
    assert (out_dir / "problem.json").exists()
    assert (out_dir / "package" / "manifest.json").exists()


def test_cli_main_entry_point_error_exit_code(tmp_path: Path, monkeypatch):
    ret = main(["non_existent_file.png"])
    assert ret == 1
