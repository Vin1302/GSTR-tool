from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from gstr_tool.core.credentials import load_credentials
from gstr_tool.core.browser import financial_year_periods
from gstr_tool.core.parsers import parse_download_folder
from gstr_tool.core.template_writer import generate_workbook


TEMPLATE = Path("/Users/vineetgarg/Downloads/GSTR template.xlsx")


class GstrToolTests(unittest.TestCase):
    def test_financial_year_periods(self):
        periods = financial_year_periods("2025-26")
        self.assertEqual(periods[0], ("042025", "Apr-2025"))
        self.assertEqual(periods[-1], ("032026", "Mar-2026"))
        self.assertEqual(len(periods), 12)

    def test_credentials_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "credentials.xlsx"
            workbook = Workbook(); sheet = workbook.active
            sheet.append(["Client Name", "GSTIN", "User ID", "Password"])
            sheet.append(["Acme", "19ABCDE1234F1Z5", "acme.user", "secret"])
            workbook.save(path)
            credentials = load_credentials(path)
            self.assertEqual(credentials[0].label, "Acme")
            self.assertEqual(credentials[0].username, "acme.user")

    def test_filters_and_formula_preservation(self):
        if not TEMPLATE.exists():
            self.skipTest("Template not available")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); downloads = root / "downloads"; downloads.mkdir()
            einvoice = Workbook(); sheet = einvoice.active
            sheet.append(["Invoice Number", "Invoice Date", "Status", "Document Type", "Taxable Value", "IGST", "CGST", "SGST", "Cess"])
            sheet.append(["INV-1", "05/04/2025", "Valid", "Invoice", 1000, 180, 0, 0, 0])
            sheet.append(["INV-2", "06/04/2025", "Cancelled", "Invoice", 9999, 1799, 0, 0, 0])
            einvoice_folder = downloads / "E-Invoice"; einvoice_folder.mkdir()
            einvoice.save(einvoice_folder / "einvoice.xlsx")

            two_b = Workbook(); sheet = two_b.active
            sheet.append(["Invoice Number", "Invoice Date", "Supplier Name", "GSTIN", "Taxable Value", "IGST", "CGST", "SGST", "Cess", "RCM", "ITC Availability", "2B Period"])
            sheet.append(["P-1", "07/04/2025", "Vendor A", "19AAAAA0000A1Z5", 500, 90, 0, 0, 0, "No", "Yes", "Apr-25"])
            sheet.append(["P-2", "08/04/2025", "Vendor B", "19BBBBB0000B1Z5", 700, 0, 63, 63, 0, "Yes", "Yes", "Apr-25"])
            sheet.append(["P-3", "09/04/2025", "Vendor C", "19CCCCC0000C1Z5", 900, 162, 0, 0, 0, "No", "No", "Apr-25"])
            two_b_folder = downloads / "GSTR-2B"; two_b_folder.mkdir()
            two_b.save(two_b_folder / "gstr2b.xlsx")

            (downloads / "gstr3b.json").write_text(json.dumps({
                "ret_period": "042025", "sup_details": {"osup_det": {"txval": 2000, "iamt": 360}},
                "itc_elg": {"itc_avl": [{"ty": "OTH", "iamt": 90}]},
            }))
            (downloads / "gstr1.json").write_text(json.dumps({
                "fp": "042025", "b2b": [{"ctin": "19AAAAA0000A1Z5", "inv": [{"inum": "S-1", "idt": "10-04-2025", "itms": [{"itm_det": {"txval": 1500, "iamt": 270}}]}]}],
            }))

            months, invoices, used = parse_download_folder(downloads)
            self.assertEqual(float(months["April"].einvoice["b2b"].taxable), 1000)
            self.assertEqual(len(invoices), 2)
            self.assertIn("other", months["April"].gstr2b)
            self.assertIn("rcm", months["April"].gstr2b)

            output = root / "result.xlsx"
            generate_workbook(TEMPLATE, output, months, invoices)
            source = load_workbook(TEMPLATE, data_only=False)
            result = load_workbook(output, data_only=False)
            self.assertEqual(result["As per E-Invoice"]["B6"].value, 1000)
            self.assertEqual(result["GSTR 2b"]["C6"].value, 90)
            self.assertEqual(result["GSTR 2b"]["G6"].value, 700)
            self.assertEqual(result["As per E-Invoice"]["N6"].value, source["As per E-Invoice"]["N6"].value)
            self.assertEqual(result["Summary"]["B7"].value, source["Summary"]["B7"].value)
            self.assertEqual(result["Invoice Wise 2B(incl CDNR)"]["K5"].value, "No")
            self.assertEqual(result["Invoice Wise 2B(incl CDNR)"]["K6"].value, "Yes")
            source.close(); result.close()


if __name__ == "__main__":
    unittest.main()
