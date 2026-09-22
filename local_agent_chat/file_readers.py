from __future__ import annotations

import logging
from pathlib import Path

import pymupdf
from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)


_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")
_BINARY_SUFFIXES = {
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".parquet",
    ".feather",
    ".pkl",
    ".pickle",
    ".zip",
    ".gz",
    ".7z",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
}


class _NoPdfTextError(ValueError):
    """The PDF opened successfully but exposed no usable text layer."""


def render_file(
    path: Path,
    *,
    offset: int = 0,
    limit: int = 200,
) -> str:
    """Render a supported file into a bounded, model-friendly text window."""
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500 lines")

    if path.suffix.casefold() == ".pdf":
        lines, details = _pdf_lines(path)
    else:
        lines = _text_lines(path)
        details = "text"

    if not lines:
        return f"File {path.name!r} contains no readable text."

    if offset >= len(lines):
        return (
            f"File {path.name!r} has {len(lines)} extracted lines; "
            f"offset {offset} is past EOF."
        )

    selected = lines[offset : offset + limit]
    rendered = [
        f"{index + 1}: {line}"
        for index, line in enumerate(selected, start=offset)
    ]
    end = offset + len(selected)

    header = (
        f"File: {path.name} | {details} | "
        f"lines {offset + 1}-{end} of {len(lines)}"
    )
    if end < len(lines):
        header += f" | continue with offset={end}"

    return header + "\n" + "\n".join(rendered)


def _text_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if _looks_binary(raw, path.suffix):
        raise ValueError(
            "Unsupported binary file type. Currently supported: text files and PDFs."
        )

    for encoding in _TEXT_ENCODINGS:
        try:
            return raw.decode(encoding).splitlines()
        except UnicodeDecodeError:
            continue

    raise ValueError("File is not valid UTF-8/UTF-8-SIG/CP1251 text")


def _pdf_lines(path: Path) -> tuple[list[str], str]:
    """Extract PDF text with a robust primary parser and a compatibility fallback.

    PyMuPDF handles a wider range of real-world PDFs (CVs, reports, exported
    office documents) than pypdf in practice. pypdf remains as a fallback so a
    parser-specific failure does not make the whole file unreadable.
    """
    failures: list[str] = []

    try:
        lines, pages = _pdf_lines_pymupdf(path)
        logger.info(
            "Extracted PDF text: file=%s parser=pymupdf pages=%s lines=%s",
            path.name,
            pages,
            len(lines),
        )
        return lines, f"PDF, {pages} pages, extractor=PyMuPDF"
    except _NoPdfTextError as error:
        failures.append(f"PyMuPDF: {error}")
    except Exception as error:
        logger.warning(
            "PyMuPDF failed for %s; falling back to pypdf: %s",
            path,
            error,
            exc_info=True,
        )
        failures.append(f"PyMuPDF: {error}")

    try:
        lines, pages = _pdf_lines_pypdf(path)
        logger.info(
            "Extracted PDF text: file=%s parser=pypdf pages=%s lines=%s",
            path.name,
            pages,
            len(lines),
        )
        return lines, f"PDF, {pages} pages, extractor=pypdf"
    except _NoPdfTextError as error:
        failures.append(f"pypdf: {error}")
    except Exception as error:
        logger.warning(
            "pypdf failed for %s: %s",
            path,
            error,
            exc_info=True,
        )
        failures.append(f"pypdf: {error}")

    # Both parsers could open/process the file but no text was available. This
    # is the common case for scans and image-only PDFs; OCR is a separate
    # capability and intentionally not hidden inside the basic file reader.
    if any("no extractable text" in item.casefold() for item in failures):
        raise ValueError(
            "PDF contains no extractable text layer. It is probably scanned or "
            "image-only; OCR is not configured yet."
        )

    details = "; ".join(failures) or "unknown PDF parsing error"
    raise ValueError(f"Unable to extract text from PDF. {details}")


def _pdf_lines_pymupdf(path: Path) -> tuple[list[str], int]:
    try:
        document = pymupdf.open(path)
    except Exception as error:
        raise ValueError(f"unable to open PDF: {error}") from error

    try:
        if document.needs_pass:
            # Empty-password PDFs exist in the wild. Try that before declaring
            # the document unreadable, mirroring the old pypdf behaviour.
            if not document.authenticate(""):
                raise ValueError("encrypted PDF requires a password")

        lines: list[str] = []
        extracted_characters = 0

        for page_number, page in enumerate(document, start=1):
            try:
                text = page.get_text("text", sort=True) or ""
            except Exception as error:
                raise ValueError(
                    f"unable to extract text from page {page_number}: {error}"
                ) from error

            page_lines = [line.rstrip() for line in text.splitlines()]
            page_lines = [line for line in page_lines if line.strip()]
            if page_lines:
                lines.append(f"[Page {page_number}]")
                lines.extend(page_lines)
                extracted_characters += sum(len(line) for line in page_lines)

        if extracted_characters == 0:
            raise _NoPdfTextError("no extractable text was found")

        return lines, document.page_count
    finally:
        document.close()


def _pdf_lines_pypdf(path: Path) -> tuple[list[str], int]:
    try:
        reader = PdfReader(str(path))
    except (PdfReadError, OSError, ValueError) as error:
        raise ValueError(f"unable to parse PDF: {error}") from error

    if reader.is_encrypted:
        try:
            result = reader.decrypt("")
        except Exception as error:
            raise ValueError("encrypted PDF requires a password") from error
        if not result:
            raise ValueError("encrypted PDF requires a password")

    lines: list[str] = []
    extracted_characters = 0

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as error:
            raise ValueError(
                f"unable to extract text from page {page_number}: {error}"
            ) from error

        page_lines = [line.rstrip() for line in text.splitlines()]
        page_lines = [line for line in page_lines if line.strip()]
        if page_lines:
            lines.append(f"[Page {page_number}]")
            lines.extend(page_lines)
            extracted_characters += sum(len(line) for line in page_lines)

    if extracted_characters == 0:
        raise _NoPdfTextError("no extractable text was found")

    return lines, len(reader.pages)


def _looks_binary(raw: bytes, suffix: str) -> bool:
    if suffix.casefold() in _BINARY_SUFFIXES:
        return True
    return b"\x00" in raw[:8192]
