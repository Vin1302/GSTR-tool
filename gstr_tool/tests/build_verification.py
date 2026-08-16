from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from openpyxl import Workbook

from gstr_tool.core.pipeline import run


ROOT = Path("outputs/gstr-tool-verification")
DOWNLOADS = ROOT / "downloads"
DOWNLOADS.mkdir(parents=True, exist_ok=True)

einvoice = Workbook(); ws = einvoice.active
ws.append(["Invoice Number", "Invoice Date", "Status", "Document Type", "Taxable Value", "IGST", "CGST", "SGST", "Cess"])
ws.append(["INV-VALID", "05/04/2025", "Valid", "Invoice", 1000, 180, 0, 0, 0])
ws.append(["INV-CANCELLED", "06/04/2025", "Cancelled", "Invoice", 9999, 1799, 0, 0, 0])
einvoice.save(DOWNLOADS / "einvoice.xlsx")

two_b = Workbook(); ws = two_b.active
ws.append(["Invoice Number", "Invoice Date", "Supplier Name", "GSTIN", "Taxable Value", "IGST", "CGST", "SGST", "Cess", "RCM", "ITC Availability", "2B Period"])
ws.append(["P-1", "07/04/2025", "Vendor A", "19AAAAA0000A1Z5", 500, 90, 0, 0, 0, "No", "Yes", "Apr-25"])
ws.append(["P-2", "08/04/2025", "Vendor B", "19BBBBB0000B1Z5", 700, 0, 63, 63, 0, "Yes", "Yes", "Apr-25"])
ws.append(["P-3", "09/04/2025", "Vendor C", "19CCCCC0000C1Z5", 900, 162, 0, 0, 0, "No", "No", "Apr-25"])
two_b.save(DOWNLOADS / "gstr2b.xlsx")

(DOWNLOADS / "gstr3b.json").write_text(json.dumps({"ret_period": "042025", "sup_details": {"osup_det": {"txval": 2000, "iamt": 360}}, "itc_elg": {"itc_avl": [{"ty": "OTH", "iamt": 90}]}}))
(DOWNLOADS / "gstr1.json").write_text(json.dumps({"fp": "042025", "b2b": [{"inv": [{"inum": "S-1", "idt": "10-04-2025", "itms": [{"itm_det": {"txval": 1500, "iamt": 270}}]}]}]}))

template = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GSTR_TEMPLATE", "")
if not template:
    raise SystemExit("Pass the GSTR template path as an argument or set GSTR_TEMPLATE.")

result = run(template, str(DOWNLOADS), str(ROOT))
print(result["output"])
