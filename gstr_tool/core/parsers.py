from __future__ import annotations

import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook

from .helpers import decimal, month_name, normalized_header, yes
from .models import Invoice2B, MonthData, TaxAmounts


def _amounts(record: dict[str, Any]) -> TaxAmounts:
    return TaxAmounts(
        taxable=decimal(record.get("txval", record.get("taxable_value", record.get("taxable")))),
        igst=decimal(record.get("iamt", record.get("igst"))),
        cgst=decimal(record.get("camt", record.get("cgst"))),
        sgst=decimal(record.get("samt", record.get("sgst"))),
        cess=decimal(record.get("csamt", record.get("cess"))),
    )


def _bucket(target: dict[str, TaxAmounts], name: str) -> TaxAmounts:
    return target.setdefault(name, TaxAmounts())


def _walk_items(invoice: dict[str, Any]) -> TaxAmounts:
    result = TaxAmounts()
    for item in invoice.get("itms", invoice.get("items", [])) or []:
        details = item.get("itm_det", item)
        result.add(_amounts(details))
    if not invoice.get("itms") and not invoice.get("items"):
        result.add(_amounts(invoice))
    return result


def parse_gstr1_json(path: str | Path, months: dict[str, MonthData]) -> None:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    root = payload.get("data", payload)
    fallback = month_name("", str(root.get("fp", "")))
    sections = {
        "b2b": "b2b", "b2ba": "b2b_amended", "b2cs": "b2c", "b2cl": "b2c",
        "b2csa": "b2c_amended", "b2cla": "b2c_amended", "cdnr": "cdn", "cdnur": "cdn",
    }
    for section, category in sections.items():
        for supplier in root.get(section, []) or []:
            invoices = supplier.get("inv", supplier.get("nt", [supplier])) or []
            for invoice in invoices:
                month = month_name(invoice.get("idt", invoice.get("nt_dt", "")), fallback)
                if not month:
                    continue
                sign = -1 if str(invoice.get("ntty", invoice.get("typ", ""))).upper().startswith("C") else 1
                if str(invoice.get("rchrg", "")).upper() == "Y" and category == "b2b":
                    category_name = "b2b_rcm"
                else:
                    category_name = category
                _bucket(months[month].gstr1, category_name).add(_walk_items(invoice), sign)
    for row in root.get("hsn", {}).get("data", root.get("hsn", [])) or []:
        _bucket(months[fallback].gstr1, "hsn").add(_amounts(row))


def parse_gstr3b_json(path: str | Path, months: dict[str, MonthData]) -> None:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    root = payload.get("data", payload)
    month = month_name("", str(root.get("ret_period", root.get("fp", ""))))
    if not month:
        return
    target = months[month].gstr3b
    sup = root.get("sup_details", root.get("supDetails", {}))
    _bucket(target, "outward_nrc").add(_amounts(sup.get("osup_det", {})))
    _bucket(target, "outward_rcm").add(_amounts(sup.get("isup_rev", {})))
    _bucket(target, "non_taxable").add(_amounts(sup.get("osup_nil_exmp", {})))
    itc = root.get("itc_elg", {})
    for row in itc.get("itc_avl", []) or []:
        key = "itc_rcm" if str(row.get("ty", "")).upper() in {"IMPG", "ISRC", "RCM"} else "itc_other"
        _bucket(target, key).add(_amounts(row))
    for row in itc.get("itc_rev", []) or []:
        _bucket(target, "itc_reversed").add(_amounts(row))
    for row in root.get("intr_ltfee", {}).get("intr_details", []) or []:
        _bucket(target, "late_fee").add(_amounts(row))


def _rows_from_excel(path: Path) -> Iterable[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            header = None
            for row in rows:
                values = [normalized_header(value) for value in row]
                if any(value in {"invoice number", "document number", "inum"} for value in values):
                    header = values
                    break
            if not header:
                continue
            for row in rows:
                record = {header[index]: value for index, value in enumerate(row) if index < len(header) and header[index]}
                if any(value not in (None, "") for value in row):
                    yield record
    finally:
        workbook.close()


def _pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def parse_einvoice_excel(path: str | Path, months: dict[str, MonthData]) -> None:
    for row in _rows_from_excel(Path(path)):
        status = str(_pick(row, "status", "invoice status", "irn status") or "").strip().lower()
        if status not in {"valid", "active"}:
            continue
        date_value = _pick(row, "invoice date", "document date", "date")
        month = month_name(date_value)
        if not month:
            continue
        document_type = str(_pick(row, "document type", "invoice type", "type") or "invoice").lower()
        category = "credit_note" if "credit" in document_type else "debit_note" if "debit" in document_type else "b2b"
        _bucket(months[month].einvoice, category).add(TaxAmounts(
            taxable=decimal(_pick(row, "taxable value", "taxable amount")),
            igst=decimal(_pick(row, "igst", "igst amount")),
            cgst=decimal(_pick(row, "cgst", "cgst amount")),
            sgst=decimal(_pick(row, "sgst", "sgst amount")),
            cess=decimal(_pick(row, "cess", "cess amount")),
        ))


def parse_2b_excel(path: str | Path, months: dict[str, MonthData], invoices: list[Invoice2B]) -> None:
    for row in _rows_from_excel(Path(path)):
        available = _pick(row, "itc availability", "itc available", "itc availability status")
        if not yes(available):
            continue
        reverse = yes(_pick(row, "reverse charge", "rcm", "supply attract reverse charge"))
        period = _pick(row, "2b period", "return period", "tax period")
        date_value = _pick(row, "document date", "invoice date", "date")
        month = month_name(period) or month_name(date_value)
        if not month:
            continue
        values = TaxAmounts(
            taxable=decimal(_pick(row, "taxable value", "taxable amount")),
            igst=decimal(_pick(row, "igst", "igst amount")),
            cgst=decimal(_pick(row, "cgst", "cgst amount")),
            sgst=decimal(_pick(row, "sgst", "sgst amount")),
            cess=decimal(_pick(row, "cess", "cess amount")),
        )
        _bucket(months[month].gstr2b, "rcm" if reverse else "other").add(values)
        invoices.append(Invoice2B(
            party=str(_pick(row, "trade legal name", "supplier name", "party") or ""),
            gstin=str(_pick(row, "gstin of supplier", "supplier gstin", "gstin") or ""),
            document_type=str(_pick(row, "document type", "invoice type") or "INVOICE"),
            document_number=str(_pick(row, "document number", "invoice number", "inum") or ""),
            document_date=str(date_value or ""), taxable=values.taxable, igst=values.igst,
            cgst=values.cgst, sgst=values.sgst, cess=values.cess,
            document_value=decimal(_pick(row, "document value", "invoice value")),
            reverse_charge="Yes" if reverse else "No", itc_availability="Yes",
            period_2a=str(_pick(row, "2a period") or ""), period_2b=str(period or ""),
            remarks=str(_pick(row, "remarks", "reason") or ""),
        ))


def discover_files(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    extracted = folder / "_extracted"
    extracted.mkdir(exist_ok=True)
    files: list[Path] = []
    for path in folder.rglob("*"):
        if extracted in path.parents or not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                relative = path.relative_to(folder).with_suffix("")
                archive.extractall(extracted / relative)
        else:
            files.append(path)
    files.extend(path for path in extracted.rglob("*") if path.is_file())
    return files


def parse_download_folder(folder: str | Path) -> tuple[dict[str, MonthData], list[Invoice2B], list[str]]:
    months: dict[str, MonthData] = defaultdict(MonthData)
    invoices: list[Invoice2B] = []
    used: list[str] = []
    for path in discover_files(folder):
        name = path.name.lower()
        try:
            if path.suffix.lower() == ".json":
                text = path.read_text(encoding="utf-8-sig", errors="ignore")[:20000].lower()
                if "sup_details" in text or "itc_elg" in text:
                    parse_gstr3b_json(path, months); used.append(path.name)
                elif '"b2b"' in text or '"b2cs"' in text:
                    parse_gstr1_json(path, months); used.append(path.name)
            elif path.suffix.lower() in {".xlsx", ".xlsm"}:
                if "2b" in name:
                    parse_2b_excel(path, months, invoices); used.append(path.name)
                elif "einvoice" in name or "e-invoice" in name or "irn" in name:
                    parse_einvoice_excel(path, months); used.append(path.name)
        except Exception as exc:  # one malformed download must not block the rest
            used.append(f"SKIPPED {path.name}: {exc}")
    return months, invoices, used
