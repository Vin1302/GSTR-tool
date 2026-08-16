from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .parsers import parse_download_folder
from .template_writer import generate_workbook


def run(template_path: str, download_folder: str, output_folder: str) -> dict[str, object]:
    months, invoices, files = parse_download_folder(download_folder)
    if not files:
        raise ValueError("No supported GSTR-1, GSTR-3B, e-Invoice or GSTR-2B downloads were found.")
    output = Path(output_folder) / f"GSTR_Reconciliation_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    generate_workbook(template_path, output, months, invoices)
    return {"output": str(output), "files": files, "invoice_count": len(invoices)}
