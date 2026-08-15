"""
Unit tests for autosetter.vision (image and PDF ingestion/normalization).
"""

from __future__ import annotations

import io
from pathlib import Path
import pytest
from PIL import Image

from autosetter.vision import (
    ImageParsingError,
    load_image_as_base64,
    validate_image_path,
)


def test_missing_file_raises_error(tmp_path: Path):
    missing = tmp_path / "non_existent.png"
    with pytest.raises(ImageParsingError) as excinfo:
        validate_image_path(missing)
    assert "File not found" in str(excinfo.value)


def test_directory_path_raises_error(tmp_path: Path):
    with pytest.raises(ImageParsingError) as excinfo:
        validate_image_path(tmp_path)
    assert "not a regular file" in str(excinfo.value)


def test_unsupported_extension_raises_error(tmp_path: Path):
    txt_file = tmp_path / "problem.txt"
    txt_file.write_text("hello")
    with pytest.raises(ImageParsingError) as excinfo:
        validate_image_path(txt_file)
    assert "Unsupported file extension" in str(excinfo.value)


def test_load_valid_png(tmp_path: Path):
    img_path = tmp_path / "test.png"
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(img_path, format="PNG")

    encoded = load_image_as_base64(img_path)
    assert isinstance(encoded, list)
    assert len(encoded) == 1
    assert isinstance(encoded[0], str)
    assert len(encoded[0]) > 0


def test_load_valid_jpg_normalized_to_png(tmp_path: Path):
    img_path = tmp_path / "test.jpg"
    img = Image.new("RGB", (80, 80), color="red")
    img.save(img_path, format="JPEG")

    encoded = load_image_as_base64(img_path)
    assert isinstance(encoded, list)
    assert len(encoded) == 1
    assert len(encoded[0]) > 0


def test_corrupt_image_raises_error(tmp_path: Path):
    corrupt_path = tmp_path / "corrupt.png"
    corrupt_path.write_bytes(b"not a valid image header")
    with pytest.raises(ImageParsingError):
        load_image_as_base64(corrupt_path)
