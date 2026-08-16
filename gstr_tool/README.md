# GSTR Tool

A modular Windows desktop MVP for downloading GST return data with a human-in-the-loop login and populating the supplied reconciliation template without replacing its formula-driven sheets.

## Current workflow

1. Choose an Excel credential file. The app accepts common headers such as `Client Name`, `GSTIN`, `User ID`, and `Password`.
2. Select a client and click **Open GST login**. Chrome opens with the username and password filled. The user completes CAPTCHA/OTP and clicks **Login**.
3. Open the Returns Dashboard and download GSTR-1, GSTR-3B and GSTR-2B files into the selected folder. Download the e-Invoice report into the same folder.
4. Choose `GSTR template.xlsx` and click **Generate GSTR workbook**.

## Business rules implemented

- e-Invoice rows are included only when status is `Valid` or `Active`; cancelled/invalid rows are skipped.
- GSTR-2B rows are included only when ITC Availability is `Yes`/eligible.
- GSTR-2B reverse-charge `Yes` and `No` rows are aggregated separately.
- Eligible invoice-level 2B rows are written to `Invoice Wise 2B(incl CDNR)`.
- Only the input tabs are populated: `As per 3B`, `As per E-Invoice`, `As per GSTR 1`, `GSTR 2b`, and the invoice-level 2B tab.
- All other template sheets and formulas are preserved and Excel is instructed to recalculate on opening.

## Install and run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r gstr_tool\requirements.txt
python -m gstr_tool.app
```

Chrome must be installed. Selenium Manager will select a compatible driver.

## Standalone Windows application — no Python for end users

The final user does **not** install Python. Build the executable once on a Windows build computer:

1. Double-click `gstr_tool\build_windows.bat`.
2. The build computer needs Python 3.11/3.12 only during compilation.
3. Distribute `gstr_tool\dist\GSTRTool.exe` together with the user's GSTR template.
4. The user double-clicks `GSTRTool.exe` and selects the credential workbook, template, download folder, and output folder.

Alternatively, run `gstr_tool\build_release.ps1` to create `gstr_tool\release\GSTRTool-Windows.zip`.

The included GitHub Actions workflow can compile the Windows executable on GitHub without requiring a Windows build computer. If this directory becomes the root of a dedicated repository, move `.github/workflows/windows-exe.yml` to the repository-level `.github/workflows/` directory.

End-user requirements are limited to:

- Windows 10/11.
- Google Chrome for GST Portal login and downloads.
- Microsoft Excel or another compatible spreadsheet application to open the generated workbook.

## Credential workbook

Recommended columns:

| Client Name | GSTIN | User ID | Password |
|---|---|---|---|
| Example Pvt Ltd | 19ABCDE1234F1Z5 | example.user | password |

The password is kept only in application memory. The tool does not log it or copy it into the generated workbook. Restrict access to the credential Excel file because it contains plaintext passwords.

## Extension points

- Portal selectors/navigation are isolated in `core/browser.py`.
- Each return format is parsed independently in `core/parsers.py`.
- Template cell mappings are isolated in `core/template_writer.py`.
- New GST reports can be added without changing the UI or existing parsers.

Direct GST portal automation is intentionally limited to preparing the login and opening the dashboard. CAPTCHA/OTP and the final login action remain manual, and download navigation may need adjustment when GSTN changes its portal UI.
