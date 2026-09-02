# GSTR Tool

A modular Windows desktop MVP for downloading GST return data with a human-in-the-loop login and populating the supplied reconciliation template without replacing its formula-driven sheets.

## Current workflow

1. Choose an Excel credential file. The app accepts common headers such as `Client Name`, `GSTIN`, `User ID`, and `Password`.
2. Select a client and click **Open GST login**. Chrome opens with the username and password filled. The user completes CAPTCHA/OTP and clicks **Login**.
3. The app detects successful login, clicks only optional **Remind me later** onboarding prompts, minimizes Chrome to the taskbar, and automatically starts the selected financial-year download. The manual download button remains available for retries.
4. Files are saved under `<base folder>/<client>/<financial year>/GSTR-1`, `GSTR-3B`, `GSTR-2B`, and `E-Invoice`.
5. Choose `GSTR template.xlsx` and click **Generate GSTR workbook**.

## What is downloaded for each period

After selecting the financial year, quarter and period and clicking SEARCH, each
period is worked in this order:

| Step | Portal path | File kept |
|---|---|---|
| 1. e-Invoice | GSTR-1 tile → **VIEW** → scroll down → **VIEW INVOICES** → *Download details from e-invoices (Excel)* | `<period>_EInvoice_*` |
| 2. GSTR-1 | back on the GSTR-1 page → **VIEW SUMMARY** → *DOWNLOAD SUMMARY (PDF)* | `<period>_GSTR1_pdf_*` |
| 3. GSTR-3B | Tile → **VIEW GSTR3B** → close the *System generated summary for GSTR-3B* dialog → scroll down → *DOWNLOAD FILED GSTR-3B* | `<period>_GSTR3B_*` |
| 4. GSTR-2B | Tile → **VIEW** → scroll down → *DOWNLOAD GSTR-2B DETAILS (EXCEL)* | `<period>_GSTR2B_excel_*` |

The GSTR-2B tile heading is rendered "Auto - drafted ITC Statement for the
month", with spaces around the hyphen, so both spellings are matched. Its
DOWNLOAD button leads to a generate-a-file page and is kept only as a fallback;
the Excel export lives on the VIEW page.

Exactly one file is taken per report. Only PDF spellings are accepted for GSTR-1,
because a bare "download summary" also matches the portal's DOWNLOAD SUMMARY
(EXCEL) button; only Excel spellings are accepted for GSTR-2B, because the JSON
button's text also ends in the word "download".

Downloads are always taken from inside the report, never from a button on the
tile itself. Tile buttons are only used to open the report, and the word
"download" is matched there on the button's whole text, so a tile control such as
"Generate JSON file to download" can never be clicked by mistake. A step that
finds no matching button records the reason in `download_status.txt` and the run
continues with the next report.

### Where downloads land

Chrome is pointed at one scratch folder for the whole run, and each file is moved
into its report folder once the step that produced it has finished. The download
directory is never switched mid-run, so a file GSTN delivers late cannot drop
into whichever report folder happens to be current. Anything still in the scratch
folder at the end is filed by matching its name (`einvoice`, `gstr2b`, `gstr3b`,
`gstr1`), and every such rescue is listed in `download_status.txt`.

The GSTR-2B Excel and the e-Invoice Excel are read directly. GSTR-1 and GSTR-3B
are only published as PDFs on these routes, so both are read with the PDF reader
in `core/pdf_reader.py`. Note that a GSTR-1 summary PDF carries section totals
rather than invoice-level rows, so `As per GSTR 1` is populated at that
granularity.

## Folder handling on repeated runs

- The client/financial-year folder is resolved from the base folder each time. If
  the chosen folder is already a client folder, a financial-year folder or one of
  the four report folders, those segments are peeled off first — a second run
  updates the same folders instead of nesting new ones inside them.
- Files already downloaded for a period are always skipped, so a re-run only
  fills the gaps. Delete a file to have it fetched again.
- ZIP archives from GSTN are expanded into a temporary directory during workbook
  generation, never into the client folder.
- `download_status.txt` is appended to, with a timestamp per run, so earlier
  attempts stay readable.

## Business rules implemented

- e-Invoice rows are skipped only when GSTN reports them cancelled/invalid; exports without a status column are included in full.
- e-Invoice invoices, debit notes and credit notes are totalled into their own template columns.
- GSTR-2B rows are included only when ITC Availability is `Yes`/eligible.
- GSTR-2B reverse-charge `Yes` and `No` rows are aggregated separately.
- GSTR-2B credit notes (the `B2B-CDNR` sheets) are stored as negative amounts so they reduce the month's ITC.
- The GST Portal's own column names (`Integrated Tax(₹)`, `State/UT Tax(₹)`, `Note number`, …) are recognised alongside plain `IGST`/`SGST` headers.
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
`pdfplumber` is required to read filed GSTR-3B PDFs; without it those periods are
reported as skipped instead of silently producing empty 3B rows.

Run the tests with the template available:

```bash
set GSTR_TEMPLATE=C:\path\to\GSTR template.xlsx
python -m unittest gstr_tool.tests.test_core
```

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

Direct GST portal automation starts after the user completes CAPTCHA/OTP and the final login action. Download navigation may need adjustment when GSTN changes its portal UI.

## Automatic download boundaries

- CAPTCHA, OTP and the final Login click remain manual and are never bypassed.
- Optional Aadhaar/e-KYC and profile/metadata reminders are dismissed using **Remind me later**; the app never accepts or submits those enrolments.
- Chrome is minimized to the taskbar once the download starts, so the run does not occupy the screen. Anti-throttling flags keep GST's JavaScript timers alive while it is minimized.
- After successful GST login, GSTR-1 (JSON + summary PDF), e-Invoice, GSTR-3B (filed PDF) and GSTR-2B (Excel) downloads are automatic for all 12 periods of the selected financial year.
- The GST Portal can generate some files asynchronously. The app waits up to two minutes for each file and records an unavailable/not-ready status when GSTN has not produced it yet; the user can retry.
- e-Invoice downloads are taken from the GSTR-1 area of the GST Returns Dashboard and stored in the separate `E-Invoice` folder.
- Portal selectors are isolated in `core/browser.py` so GSTN UI changes can be updated without changing workbook logic.
- Every run creates `download_status.txt` in the client/FY folder, listing each successful, unavailable, or failed period/report download.
