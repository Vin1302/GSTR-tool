"""Read the filed GSTR-3B and GSTR-1 summary PDFs the GST Portal produces.

GSTN does not offer a machine-readable download for a filed GSTR-3B, so the PDF
is the only source for the ``As per 3B`` sheet. The layout is a fixed set of
numbered tables, which makes a line-oriented reader more robust than table
extraction: GSTN changes column widths between releases far more often than it
changes the row captions.
"""

from __future__ import annotations

import re
from pathlib import Path

from .helpers import decimal, month_name
from .models import TaxAmounts


NUMBER = re.compile(r"-?[\d,]+\.\d{2}|-?[\d,]{1,}(?=\s|$)")


def extract_text(path: str | Path) -> str:
    """Return the text of a PDF, or an empty string when no reader is installed."""
    path = Path(path)
    try:
        import pdfplumber
    except ImportError:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    with pdfplumber.open(str(path)) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def _numbers(line: str) -> list:
    """Return the numeric cells of a PDF table line, left to right."""
    values = []
    for token in re.findall(r"-?[\d,]+(?:\.\d+)?", line):
        # Table captions carry their own numbering, e.g. "3.1(a)" or "4(A)(5)".
        if re.fullmatch(r"\d", token):
            continue
        values.append(decimal(token))
    return values


def _row(text: str, *patterns: str) -> list:
    """Find the first line matching any caption pattern and return its numbers."""
    for line in text.splitlines():
        squashed = " ".join(line.split())
        for pattern in patterns:
            if re.search(pattern, squashed, flags=re.IGNORECASE):
                values = _numbers(squashed[re.search(pattern, squashed, flags=re.IGNORECASE).end():])
                if values:
                    return values
    return []


def _amounts(values: list, *, taxable: bool = True) -> TaxAmounts:
    """Map a table line's trailing numbers onto taxable/IGST/CGST/SGST/Cess."""
    if not values:
        return TaxAmounts()
    wanted = 5 if taxable else 4
    cells = values[-wanted:] if len(values) >= wanted else values + [decimal(0)] * (wanted - len(values))
    if taxable:
        return TaxAmounts(taxable=cells[0], igst=cells[1], cgst=cells[2], sgst=cells[3], cess=cells[4])
    return TaxAmounts(igst=cells[0], cgst=cells[1], sgst=cells[2], cess=cells[3])


def gstr3b_period(text: str, fallback: str = "") -> str:
    """Read the return period printed on the filed GSTR-3B PDF."""
    match = re.search(r"(?:Year|Period)\s*[:\-]?\s*([A-Za-z]{3,9})\s*[-/ ]\s*(\d{2,4})", text)
    if match:
        return month_name(f"{match.group(1)}-{match.group(2)[-2:]}")
    return month_name("", fallback)


def parse_gstr3b_text(text: str) -> dict[str, TaxAmounts]:
    """Return the ``As per 3B`` buckets found in a filed GSTR-3B PDF."""
    buckets: dict[str, TaxAmounts] = {}

    # Table 3.1 — outward supplies and inward supplies liable to reverse charge.
    buckets["outward_nrc"] = _amounts(_row(text, r"\(a\)\s*Outward taxable supplies\s*\(other than"))
    nil_rated = _amounts(_row(text, r"\(c\)\s*Other outward supplies\s*\(Nil rated"))
    non_gst = _amounts(_row(text, r"\(e\)\s*Non-?GST outward supplies"))
    nil_rated.add(non_gst)
    buckets["non_taxable"] = nil_rated
    buckets["outward_rcm"] = _amounts(_row(text, r"\(d\)\s*Inward supplies\s*\(liable to reverse charge"))

    # Table 4 — eligible ITC. RCM/import ITC is tracked apart from other ITC.
    itc_rcm = _amounts(_row(text, r"\(1\)\s*Import of goods"), taxable=False)
    itc_rcm.add(_amounts(_row(text, r"\(2\)\s*Import of services"), taxable=False))
    itc_rcm.add(_amounts(_row(text, r"\(3\)\s*Inward supplies liable to reverse charge"), taxable=False))
    buckets["itc_rcm"] = itc_rcm
    buckets["itc_other"] = _amounts(_row(text, r"\(5\)\s*All other ITC"), taxable=False)

    reversed_itc = _amounts(_row(text, r"\(1\)\s*As per rules"), taxable=False)
    reversed_itc.add(_amounts(_row(text, r"\(2\)\s*Others?\b"), taxable=False))
    buckets["itc_reversed"] = reversed_itc

    # Table 5.1 — interest and late fee.
    buckets["late_fee"] = _amounts(_row(text, r"Late fee"), taxable=False)
    return {key: value for key, value in buckets.items() if _has_value(value)}


def _has_value(amounts: TaxAmounts) -> bool:
    return any((amounts.taxable, amounts.igst, amounts.cgst, amounts.sgst, amounts.cess))


def parse_gstr3b_pdf(path: str | Path, months, fallback_period: str = "") -> str:
    """Populate ``months[<month>].gstr3b`` from a filed GSTR-3B PDF. Returns the month."""
    text = extract_text(path)
    if not text.strip():
        raise ValueError("no readable text (install pdfplumber to read GST PDFs)")
    month = gstr3b_period(text, fallback_period)
    if not month:
        raise ValueError("return period was not found in the PDF")
    target = months[month].gstr3b
    for key, amounts in parse_gstr3b_text(text).items():
        target.setdefault(key, TaxAmounts()).add(amounts)
    return month


GSTR1_SECTIONS = (
    ("b2b", r"^4A(?:,\s*4B)?"),
    ("b2b_rcm", r"^4B\b"),
    ("b2b_amended", r"^9A\s*-\s*Amended B2B|^9A\b.*B2B"),
    ("b2c", r"^5A\b|^7\b"),
    ("b2c_amended", r"^10\b"),
    ("cdn", r"^9B\b"),
    ("hsn", r"^12\b|HSN"),
)


def parse_gstr1_summary_pdf(path: str | Path, months, fallback_period: str = "") -> str:
    """Populate ``months[<month>].gstr1`` from a GSTR-1 summary PDF.

    This is a fallback for periods where GSTN did not produce the filed JSON.
    The JSON is preferred because the PDF only carries section totals.
    """
    text = extract_text(path)
    if not text.strip():
        raise ValueError("no readable text (install pdfplumber to read GST PDFs)")
    month = gstr3b_period(text, fallback_period)
    if not month:
        raise ValueError("return period was not found in the PDF")
    target = months[month].gstr1
    for key, pattern in GSTR1_SECTIONS:
        values = _row(text, pattern)
        if not values:
            continue
        # Summary lines read: <no. of records> <value> <taxable> <igst> <cgst> <sgst> <cess>
        amounts = _amounts(values)
        if _has_value(amounts):
            target.setdefault(key, TaxAmounts()).add(amounts)
    return month
