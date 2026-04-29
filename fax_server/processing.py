from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


TIFF_EXTENSIONS = {".tif", ".tiff"}
OFFICE_EXTENSIONS = {
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
}


class ConversionError(RuntimeError):
    pass


def normalize_to_tiff(source: Path, destination: Path, convert_bin: str, office_convert_bin: str | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    conversion_source = source

    if source.suffix.lower() in OFFICE_EXTENSIONS:
        if not office_convert_bin:
            raise ConversionError("office document conversion requires LibreOffice/soffice")
        conversion_source = _convert_office_to_pdf(source, office_convert_bin)

    command = [
        convert_bin,
        "-density",
        "204x196",
        str(conversion_source),
        "-auto-orient",
        "-resize",
        "1728x",
        "-units",
        "PixelsPerInch",
        "-density",
        "204x196",
        "-colorspace",
        "Gray",
        "-monochrome",
        "-compress",
        "Fax",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise ConversionError(result.stderr.strip() or result.stdout.strip() or "document conversion failed")
    return destination


def _convert_office_to_pdf(source: Path, office_convert_bin: str) -> Path:
    output_dir = Path(tempfile.mkdtemp(prefix="ccc-fax-office-"))
    command = [
        office_convert_bin,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(source),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode != 0:
        raise ConversionError(result.stderr.strip() or result.stdout.strip() or "office document conversion failed")
    pdf_path = output_dir / f"{source.stem}.pdf"
    if not pdf_path.exists():
        matches = list(output_dir.glob("*.pdf"))
        if not matches:
            raise ConversionError("office document conversion did not produce a PDF")
        pdf_path = matches[0]
    return pdf_path
