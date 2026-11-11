"""Extract plain text from CV files with strict safety limits."""
import io
import multiprocessing
import os
import zipfile
from pathlib import Path

from django.conf import settings


class ExtractionError(Exception):
    """Raised when a CV cannot be extracted safely."""


CV_ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.txt'}


def _read_pdf_worker(path: str, max_pages: int, max_chars: int, queue):
    """Run in a separate process so a hostile PDF cannot hang the app."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ExtractionError("Encrypted PDFs are not supported")
        if len(reader.pages) > max_pages:
            raise ExtractionError(
                f"PDF has {len(reader.pages)} pages; maximum is {max_pages}"
            )
        parts = []
        for page in reader.pages[:max_pages]:
            text = page.extract_text() or ''
            parts.append(text)
        text = '\n'.join(parts).strip()
        if len(text) > max_chars:
            raise ExtractionError(
                f"Extracted text exceeds {max_chars} characters"
            )
        queue.put(('ok', text))
    except Exception as exc:
        queue.put(('error', str(exc)))


def _extract_pdf_text(path: str) -> str:
    max_pages = getattr(settings, 'CV_MAX_PAGES', 10)
    max_chars = getattr(settings, 'CV_MAX_TEXT_CHARS', 30000)
    timeout = getattr(settings, 'CV_PARSE_TIMEOUT_SECONDS', 15)

    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    process = ctx.Process(
        target=_read_pdf_worker,
        args=(path, max_pages, max_chars, queue),
    )
    process.start()
    process.join(timeout=timeout)

    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
        raise ExtractionError("PDF parsing timed out; file may be malformed")

    try:
        status, payload = queue.get_nowait()
    except Exception as exc:
        raise ExtractionError(f"PDF parsing failed: {exc}") from exc

    if status == 'error':
        raise ExtractionError(payload)
    return payload


def _check_docx_size(path: str, max_size_mb: int):
    if not zipfile.is_zipfile(path):
        raise ExtractionError("DOCX file is not a valid zip archive")
    max_uncompressed = max_size_mb * 1024 * 1024
    total_uncompressed = 0
    with zipfile.ZipFile(path, 'r') as zf:
        for info in zf.infolist():
            total_uncompressed += info.file_size
            if total_uncompressed > max_uncompressed:
                raise ExtractionError(
                    f"DOCX uncompressed size exceeds {max_size_mb} MB"
                )


def _extract_docx_text(path: str) -> str:
    max_size_mb = getattr(settings, 'CV_MAX_DOCX_SIZE_MB', 20)
    max_chars = getattr(settings, 'CV_MAX_TEXT_CHARS', 30000)

    _check_docx_size(path, max_size_mb)

    try:
        from docx import Document
    except ImportError as exc:
        raise ExtractionError(
            "DOCX support is not installed"
        ) from exc

    document = Document(path)
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    text = '\n'.join(parts).strip()
    if len(text) > max_chars:
        raise ExtractionError(f"Extracted text exceeds {max_chars} characters")
    return text


def _extract_txt_text(path: str) -> str:
    max_chars = getattr(settings, 'CV_MAX_TEXT_CHARS', 30000)
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read(max_chars + 1)
    if len(text) > max_chars:
        raise ExtractionError(f"Extracted text exceeds {max_chars} characters")
    return text.strip()


def _magic_matches(path: str, expected_prefix: bytes) -> bool:
    try:
        with open(path, 'rb') as f:
            header = f.read(len(expected_prefix))
        return header == expected_prefix
    except OSError:
        return False


def extract_text_from_file(path: str) -> str:
    """Return plain text from a CV file, enforcing caps and magic-byte checks."""
    ext = Path(path).suffix.lower()
    if ext not in CV_ALLOWED_EXTENSIONS:
        raise ExtractionError(f"Unsupported file type: {ext}")

    if ext == '.pdf':
        if not _magic_matches(path, b'%PDF-'):
            raise ExtractionError("File does not have a valid PDF header")
        return _extract_pdf_text(path)

    if ext == '.docx':
        if not _magic_matches(path, b'PK'):
            raise ExtractionError("File does not have a valid DOCX header")
        return _extract_docx_text(path)

    return _extract_txt_text(path)
