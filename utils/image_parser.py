"""
image_parser.py
================
Responsible for turning a *local* path (png / jpg / jpeg / pdf) into one or
more base64-encoded PNG byte-strings that can be handed to Ollama's vision
API (`images=[...]` field in a chat message).

Design notes
------------
- We standardize everything to PNG bytes before base64-encoding, because
  Ollama's multimodal endpoint expects raw image bytes (base64 str) per
  image, and PNG is a safe, lossless, universally-supported format.
- PDFs are rasterized page-by-page using PyMuPDF (fitz), which is a pure
  offline dependency (no poppler/system binary required). Each page becomes
  one image in the returned list, in reading order.
- We never touch the network here; everything is local file I/O.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import List

# Pillow is used to validate/normalize raster images (png/jpg/jpeg) into PNG.
from PIL import Image, UnidentifiedImageError

# PyMuPDF is used to rasterize PDF pages into images, fully offline.
# Newer PyMuPDF releases prefer `import pymupdf`, but still ship the
# classic `fitz` module name for backwards compatibility, which triggers a
# harmless deprecation warning. We import via `pymupdf` when available and
# fall back to `fitz` for older installs, so no warning is printed either way.
try:
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover - fallback for older PyMuPDF versions
    import fitz  # type: ignore

# Formats we explicitly support, per the project spec.
SUPPORTED_RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = SUPPORTED_RASTER_EXTENSIONS | SUPPORTED_PDF_EXTENSIONS

# Rendering resolution (DPI) used when rasterizing PDF pages. Higher DPI
# improves OCR/vision accuracy for dense problem statements at the cost of
# larger payloads sent to the model.
PDF_RENDER_DPI = 200


class ImageParsingError(Exception):
    """Raised when the input file cannot be read, is unsupported, or is corrupt."""


def _validate_path(image_path: Path) -> None:
    """Ensure the given path exists, is a file, and has a supported extension."""
    if not image_path.exists():
        raise ImageParsingError(f"File not found: {image_path}")

    if not image_path.is_file():
        raise ImageParsingError(f"Path is not a file: {image_path}")

    suffix = image_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ImageParsingError(
            f"Unsupported file extension '{suffix}'. "
            f"Supported extensions are: {sorted(SUPPORTED_EXTENSIONS)}"
        )


def _pil_image_to_base64_png(pil_image: Image.Image) -> str:
    """Convert a PIL Image object to a base64-encoded PNG string."""
    import io

    # Normalize color mode: some scanned images are CMYK/P/etc, which PNG
    # encoders can choke on. Converting to RGB is a safe, lossless-enough
    # normalization for OCR/vision purposes.
    if pil_image.mode not in ("RGB", "RGBA"):
        pil_image = pil_image.convert("RGB")

    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _load_raster_image(image_path: Path) -> List[str]:
    """Load a single png/jpg/jpeg file and return it as a one-element list
    of base64-encoded PNG strings."""
    try:
        with Image.open(image_path) as img:
            img.load()  # force-read pixel data now, so corrupt files fail fast
            encoded = _pil_image_to_base64_png(img)
    except UnidentifiedImageError as exc:
        raise ImageParsingError(f"Could not identify image file: {image_path}") from exc
    except OSError as exc:
        raise ImageParsingError(f"Failed to open image file: {image_path} ({exc})") from exc

    return [encoded]


def _load_pdf_pages_as_images(pdf_path: Path) -> List[str]:
    """Rasterize every page of a PDF into base64-encoded PNG strings."""
    encoded_pages: List[str] = []

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # PyMuPDF raises its own exception types
        raise ImageParsingError(f"Failed to open PDF file: {pdf_path} ({exc})") from exc

    try:
        if doc.page_count == 0:
            raise ImageParsingError(f"PDF has no pages: {pdf_path}")

        # zoom factor to reach the target DPI (PDF default is 72 DPI)
        zoom = PDF_RENDER_DPI / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            png_bytes = pixmap.tobytes("png")
            encoded_pages.append(base64.b64encode(png_bytes).decode("utf-8"))
    finally:
        doc.close()

    return encoded_pages


def load_image_as_base64(image_path: str | Path) -> List[str]:
    """
    Public entry point of this module.

    Parameters
    ----------
    image_path : str | Path
        Local path to a .png/.jpg/.jpeg/.pdf file containing the problem
        statement (possibly spanning multiple pages for PDFs).

    Returns
    -------
    List[str]
        A list of base64-encoded PNG image strings, ready to be passed to
        Ollama's chat `images=[...]` argument. For raster inputs this list
        has exactly one element; for PDFs it has one element per page.

    Raises
    ------
    ImageParsingError
        If the file is missing, unsupported, or cannot be decoded.
    """
    path = Path(image_path).expanduser().resolve()
    _validate_path(path)

    suffix = path.suffix.lower()
    if suffix in SUPPORTED_RASTER_EXTENSIONS:
        return _load_raster_image(path)
    if suffix in SUPPORTED_PDF_EXTENSIONS:
        return _load_pdf_pages_as_images(path)

    # Defensive fallback; _validate_path should already have caught this.
    raise ImageParsingError(f"Unsupported file extension '{suffix}'")
