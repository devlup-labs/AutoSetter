"""
autosetter.vision
=================
Image and PDF ingestion, normalization, and base64 encoding for vision models.

Transforms input files (.png, .jpg, .jpeg, .pdf) into base64-encoded PNG payloads
suitable for Ollama's multimodal vision API.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import List

from PIL import Image, UnidentifiedImageError

try:
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from autosetter.config import (
    PDF_RENDER_DPI,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_PDF_EXTENSIONS,
    SUPPORTED_RASTER_EXTENSIONS,
)


class ImageParsingError(Exception):
    """Raised when an input image or PDF is missing, unsupported, or corrupt."""


def validate_image_path(image_path: Path) -> None:
    """Validate that the path exists, is a file, and has a supported extension."""
    if not image_path.exists():
        raise ImageParsingError(f"File not found: {image_path}")

    if not image_path.is_file():
        raise ImageParsingError(f"Path is not a regular file: {image_path}")

    suffix = image_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ImageParsingError(
            f"Unsupported file extension '{suffix}'. "
            f"Supported extensions: {sorted(SUPPORTED_EXTENSIONS)}"
        )


def _pil_image_to_base64_png(pil_image: Image.Image) -> str:
    """Normalize a PIL Image to RGB/RGBA and encode it as a base64 PNG string."""
    if pil_image.mode not in ("RGB", "RGBA"):
        pil_image = pil_image.convert("RGB")

    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _load_raster_image(image_path: Path) -> List[str]:
    """Load a single raster image file and return its base64 PNG representation."""
    try:
        with Image.open(image_path) as img:
            img.load()  # Force reading pixel data to catch corruption early
            encoded = _pil_image_to_base64_png(img)
    except UnidentifiedImageError as exc:
        raise ImageParsingError(f"Could not identify image file: {image_path}") from exc
    except OSError as exc:
        raise ImageParsingError(f"Failed to open image file: {image_path} ({exc})") from exc

    return [encoded]


def _load_pdf_pages_as_images(pdf_path: Path, dpi: int = PDF_RENDER_DPI) -> List[str]:
    """Rasterize each page of a PDF file into base64-encoded PNG strings."""
    encoded_pages: List[str] = []

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ImageParsingError(f"Failed to open PDF file: {pdf_path} ({exc})") from exc

    try:
        if doc.page_count == 0:
            raise ImageParsingError(f"PDF contains no pages: {pdf_path}")

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            png_bytes = pixmap.tobytes("png")
            encoded_pages.append(base64.b64encode(png_bytes).decode("utf-8"))
    finally:
        doc.close()

    return encoded_pages


def load_image_as_base64(image_path: str | Path, dpi: int = PDF_RENDER_DPI) -> List[str]:
    """
    Load a problem statement image or PDF and convert it to base64 PNG strings.

    Parameters
    ----------
    image_path : str | Path
        Local file path to a .png, .jpg, .jpeg, or .pdf file.
    dpi : int
        Rendering DPI used for PDF rasterization.

    Returns
    -------
    List[str]
        List of base64-encoded PNG strings (one for raster images, one per page for PDFs).

    Raises
    ------
    ImageParsingError
        If the file cannot be loaded, has an invalid format, or is corrupted.
    """
    path = Path(image_path).expanduser().resolve()
    validate_image_path(path)

    suffix = path.suffix.lower()
    if suffix in SUPPORTED_RASTER_EXTENSIONS:
        return _load_raster_image(path)
    if suffix in SUPPORTED_PDF_EXTENSIONS:
        return _load_pdf_pages_as_images(path, dpi=dpi)

    raise ImageParsingError(f"Unsupported file extension '{suffix}'")
