from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .models import GstCredential


LOG = logging.getLogger(__name__)
GST_LOGIN_URL = "https://services.gst.gov.in/services/login"
GST_DASHBOARD_URL = "https://services.gst.gov.in/services/auth/dashboard"
GST_LOGOUT_URL = "https://services.gst.gov.in/services/logout"


def financial_year_periods(financial_year: str) -> list[tuple[str, str, str]]:
    """Return (month name, quarter number, readable label) for Apr-Mar."""
    match = re.fullmatch(r"(\d{4})-(\d{2}|\d{4})", financial_year.strip())
    if not match:
        raise ValueError("Financial year must look like 2025-26.")
    start = int(match.group(1))
    end = start + 1
    periods = []
    for month in range(4, 13):
        periods.append((datetime(start, month, 1).strftime("%B"), str(((month - 4) // 3) + 1),
                        datetime(start, month, 1).strftime("%b-%Y")))
    for month in range(1, 4):
        periods.append((datetime(end, month, 1).strftime("%B"), "4",
                        datetime(end, month, 1).strftime("%b-%Y")))
    return periods


REPORT_FOLDERS = ("GSTR-1", "GSTR-3B", "GSTR-2B", "E-Invoice")


class GstBrowserSession:
    """Selenium-backed, human-in-the-loop GST Portal session.

    CAPTCHA, OTP and the final Login click remain with the user. Selectors and
    navigation are isolated here because the GST Portal can change its UI.
    """

    def __init__(self, download_dir: str | Path, skip_existing: bool = True):
        self.download_dir = Path(download_dir).resolve()
        self.skip_existing = skip_existing
        self.driver = None

    def open_login(self, credential: GstCredential) -> None:
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as ec
        except ImportError as exc:
            raise RuntimeError("Selenium is not installed. Run: pip install -r gstr_tool/requirements.txt") from exc

        self.download_dir.mkdir(parents=True, exist_ok=True)
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        # GST pages rely on JavaScript timers and asynchronous rendering. Keep
        # those active if the user chooses to minimize the automated browser.
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--start-maximized")
        options.add_experimental_option("prefs", {
            "download.default_directory": str(self.download_dir),
            "download.prompt_for_download": False,
            "safebrowsing.enabled": True,
        })
        self.driver = webdriver.Chrome(options=options)
        self.driver.get(GST_LOGIN_URL)
        wait = WebDriverWait(self.driver, 30)
        username = wait.until(ec.visibility_of_element_located((By.ID, "username")))
        password = wait.until(ec.visibility_of_element_located((By.ID, "user_pass")))
        username.clear()
        username.send_keys(credential.username)
        password.clear()
        password.send_keys(credential.password)
        LOG.info("GST login page prepared for %s", credential.label)

    def is_logged_in(self) -> bool:
        if self.driver is None:
            return False
        url = (self.driver.current_url or "").lower()
        return "login" not in url and "gst.gov.in" in url

    def open_returns_dashboard(self) -> None:
        if not self.is_logged_in():
            raise RuntimeError("Complete CAPTCHA and click Login in the browser first.")
        self.navigate_returns_dashboard()

    def dismiss_post_login_prompts(self, timeout: int = 12) -> list[str]:
        """Dismiss non-mandatory GST onboarding reminders without accepting anything."""
        if self.driver is None:
            return []
        dismissed: list[str] = []
        deadline = time.time() + timeout
        idle_checks = 0
        labels = ["remind me later", "maybe later", "skip for now", "no thanks"]
        while time.time() < deadline:
            clicked = False
            for label in labels:
                if self._click_text([label]):
                    dismissed.append(label)
                    clicked = True
                    idle_checks = 0
                    time.sleep(1)
                    break
            if not clicked:
                idle_checks += 1
                if idle_checks >= 4:
                    break
                time.sleep(0.5)
        return dismissed

    def minimize_browser(self) -> None:
        """Minimize Chrome to the taskbar so the run does not occupy the screen.

        The anti-throttling flags set in ``open_login`` keep GST's JavaScript
        timers alive while the window is minimized. If the window manager
        refuses to minimize, Chrome is moved off-screen instead so the download
        still proceeds unattended.
        """
        if self.driver is None:
            return
        try:
            self.driver.minimize_window()
        except Exception:
            try:
                self.driver.set_window_position(-2000, 0)
            except Exception:
                LOG.warning("Chrome could not be minimized; it stays on screen")

    def restore_browser(self) -> None:
        if self.driver is not None:
            try:
                self.driver.maximize_window()
            except Exception:
                self.driver.set_window_position(0, 0)

    def _set_download_directory(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": str(folder.resolve()),
        })

    def _select(self, candidates: list[str], *, text: str | None = None,
                value: str | None = None) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select

        for xpath in candidates:
            elements = self.driver.find_elements(By.XPATH, xpath)
            for element in elements:
                if not element.is_displayed():
                    continue
                selector = Select(element)
                attempts = []
                if text:
                    labels = self._financial_year_labels(text) if re.fullmatch(r"\d{4}-\d{2,4}", text) else [text]
                    for label in labels:
                        attempts.append(lambda label=label: selector.select_by_visible_text(label))
                if value:
                    attempts.append(lambda: selector.select_by_value(value))
                for attempt in attempts:
                    try:
                        attempt(); return
                    except Exception:  # try the next portal representation
                        continue
                if text:
                    start_year = re.sub(r"\D", "", text)[:4]
                    expected_end = str(int(start_year) + 1) if len(start_year) == 4 else ""
                    for option in selector.options:
                        if text.lower() in option.text.lower():
                            selector.select_by_visible_text(option.text)
                            return
                        digits = re.sub(r"\D", "", option.text)
                        if digits.startswith(start_year) and expected_end and digits.endswith(expected_end):
                            selector.select_by_visible_text(option.text)
                            return
        raise RuntimeError(f"GST Portal selection was not found: {text or value}")

    @staticmethod
    def _financial_year_labels(value: str) -> list[str]:
        start = int(value[:4])
        end = start + 1
        return [f"{start}-{end}", f"{start} - {end}", f"{start}-{str(end)[-2:]}", f"{start} - {str(end)[-2:]}"]

    def navigate_returns_dashboard(self) -> None:
        """Navigate through Services → Returns → Returns Dashboard."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.action_chains import ActionChains

        # Never load an authenticated GST route directly. GSTN rejects direct
        # navigation with "Access Denied" even when the browser is logged in.
        if self._has_visible_period_selects():
            return
        self.dismiss_post_login_prompts()

        def visible_exact(label: str):
            xpath = (
                "//*[self::a or self::button or self::span]"
                f"[translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{label.lower()}']"
            )
            return [item for item in self.driver.find_elements(By.XPATH, xpath) if item.is_displayed()]

        services = visible_exact("Services")
        if services:
            ActionChains(self.driver).move_to_element(services[0]).click().perform()
            time.sleep(1)
        returns = visible_exact("Returns")
        if returns:
            ActionChains(self.driver).move_to_element(returns[0]).click().perform()
            time.sleep(1)
        dashboard = visible_exact("Returns Dashboard")
        if not dashboard:
            raise RuntimeError("Services → Returns → Returns Dashboard menu was not found.")
        dashboard[0].click()
        time.sleep(3)
        self.dismiss_post_login_prompts()

    def _has_visible_period_selects(self) -> bool:
        from selenium.webdriver.common.by import By

        selects = self.driver.find_elements(By.XPATH, "//select")
        visible_text = " ".join(
            (element.get_attribute("id") or "") + " " + (element.get_attribute("name") or "")
            for element in selects if element.is_displayed()
        ).lower()
        page_text = (self.driver.find_element(By.TAG_NAME, "body").text or "").lower()
        return len([element for element in selects if element.is_displayed()]) >= 3 and (
            "financial year" in page_text and "quarter" in page_text and "period" in page_text
        ) and "access denied" not in page_text

    def _click_text(self, labels: list[str], root=None) -> bool:
        from selenium.webdriver.common.by import By

        context = root or self.driver
        for label in labels:
            literal = label.lower()
            xpath = (
                ".//*[self::button or self::a or @role='button']"
                f"[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{literal}')]"
            )
            for element in context.find_elements(By.XPATH, xpath):
                if element.is_displayed() and element.is_enabled():
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                    time.sleep(0.3)
                    try:
                        element.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", element)
                    return True
        return False

    def _wait_and_click_text(self, labels: list[str], timeout: int = 60, root=None) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if root is None:
                self.dismiss_post_login_prompts(timeout=1)
            try:
                if self._click_text(labels, root):
                    return True
            except Exception:
                if root is not None:
                    return False
            time.sleep(1)
        return False

    def _click_returns_search(self) -> bool:
        """Click only the File Returns SEARCH button, never Search Taxpayer."""
        from selenium.webdriver.common.by import By

        upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        lower = "abcdefghijklmnopqrstuvwxyz"
        xpath = (
            f"//button[translate(normalize-space(.),'{upper}','{lower}')='search']"
            f" | //input[@type='submit' and translate(normalize-space(@value),'{upper}','{lower}')='search']"
            f" | //*[@role='button' and translate(normalize-space(.),'{upper}','{lower}')='search']"
        )
        for element in self.driver.find_elements(By.XPATH, xpath):
            if not element.is_displayed() or not element.is_enabled():
                continue
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            time.sleep(0.3)
            try:
                element.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", element)
            return True
        return False

    def _click_exact_button(self, labels: list[str]) -> bool:
        from selenium.webdriver.common.by import By

        upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        lower = "abcdefghijklmnopqrstuvwxyz"
        for label in labels:
            literal = label.strip().lower()
            xpath = (
                f"//button[translate(normalize-space(.),'{upper}','{lower}')='{literal}']"
                f" | //a[translate(normalize-space(.),'{upper}','{lower}')='{literal}']"
                f" | //input[translate(normalize-space(@value),'{upper}','{lower}')='{literal}']"
            )
            for element in self.driver.find_elements(By.XPATH, xpath):
                if element.is_displayed() and element.is_enabled():
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                    try:
                        element.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", element)
                    return True
        return False

    def _return_to_monthly_tiles(self, results_url: str) -> None:
        """Use GST BACK controls until the selected month's tiles are restored."""
        monthly_tile_labels = ["monthly return gstr-3b", "auto-drafted itc statement"]
        for _ in range(3):
            if self._tile(monthly_tile_labels) is not None:
                break
            if self._click_exact_button(["back"]):
                time.sleep(4)
                continue
            if self.driver.current_url != results_url:
                self.driver.back()
                time.sleep(4)
                continue
            break
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

    def _tile(self, labels: list[str]):
        from selenium.webdriver.common.by import By

        for label in labels:
            needle = label.lower()
            xpath = (
                "//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
                f"'{needle}')]/ancestor::div[.//button or .//a][1]"
            )
            visible = [element for element in self.driver.find_elements(By.XPATH, xpath) if element.is_displayed()]
            if visible:
                return visible[0]
        return None

    def _prepare_period(self, financial_year: str, quarter: str, month: str) -> None:
        """Open File Returns and reproduce the GST Portal's FY/quarter/month flow."""
        self.navigate_returns_dashboard()
        self._select([
            "//select[contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'fin')]",
            "//select[contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'fin')]",
            "//label[contains(.,'Financial Year')]/following::select[1]",
        ], text=financial_year)
        time.sleep(1)
        self._select([
            "//select[contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'quarter')]",
            "//select[contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'quarter')]",
            "//label[contains(.,'Quarter')]/following::select[1]",
        ], text=f"Quarter {quarter}")
        time.sleep(1)
        self._select([
            "//select[contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'period')]",
            "//select[contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'period')]",
            "//label[contains(.,'Period')]/following::select[1]",
        ], text=month)
        if not self._click_returns_search():
            raise RuntimeError("GST Portal SEARCH button was not found.")
        time.sleep(4)
        self.dismiss_post_login_prompts()
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

    @staticmethod
    def _snapshot(folder: Path) -> set[Path]:
        return {path for path in folder.glob("*") if path.is_file()}

    def _wait_for_download(self, folder: Path, before: set[Path], timeout: int = 120) -> Path | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            partials = list(folder.glob("*.crdownload")) + list(folder.glob("*.tmp"))
            completed = [path for path in folder.glob("*") if path.is_file() and path not in before and path not in partials]
            if completed and not partials:
                return max(completed, key=lambda path: path.stat().st_mtime)
            time.sleep(1)
        return None

    @staticmethod
    def _stored_name(report: str, period_label: str) -> str:
        return f"{period_label}_{report.replace('-', '')}_"

    def _existing_download(self, folder: Path, report: str, period_label: str,
                           kind: str = "") -> Path | None:
        """Return a file this period already produced so re-runs do not duplicate it."""
        prefix = self._stored_name(report, period_label) + (f"{kind}_" if kind else "")
        for path in sorted(folder.glob(f"{prefix}*")):
            if path.is_file() and path.stat().st_size > 0:
                return path
        return None

    def _store(self, downloaded: Path, report: str, period_label: str, kind: str = "") -> Path:
        """Rename a fresh download so period, report and file kind stay obvious."""
        prefix = self._stored_name(report, period_label) + (f"{kind}_" if kind else "")
        if downloaded.name.startswith(prefix):
            return downloaded
        target = downloaded.with_name(prefix + downloaded.name)
        counter = 2
        while target.exists():
            target = downloaded.with_name(f"{prefix}{counter}_{downloaded.name}")
            counter += 1
        downloaded.rename(target)
        return target

    def _download_here(self, report: str, period_label: str, folder: Path,
                       download_labels: list[str], *, kind: str = "",
                       generated: bool = False, timeout: int = 120) -> str:
        """Click a download control on the page that is already open and keep the file.

        ``generated`` covers the GST Portal pattern where the first click only asks
        GSTN to build the file and a second link appears once it is ready.
        """
        label = f"{report}{' ' + kind.upper() if kind else ''} {period_label}"
        if self.skip_existing:
            existing = self._existing_download(folder, report, period_label, kind)
            if existing is not None:
                return f"{label}: already downloaded ({existing.name})"
        self._set_download_directory(folder)
        before = self._snapshot(folder)
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
        if not self._wait_and_click_text(download_labels, timeout=25):
            return f"{label}: download action not available on this page"
        if generated:
            # GSTN builds the archive asynchronously and then shows a second link.
            # Some periods download straight away, so a missing link is not an
            # error — the file wait below decides the outcome either way.
            self._wait_and_click_text(
                ["click here to download", "download generated file", "generated file"],
                timeout=60,
            )
        downloaded = self._wait_for_download(folder, before, timeout=timeout)
        if downloaded is None:
            return f"{label}: file was not ready within {timeout} seconds"
        return f"{label}: {self._store(downloaded, report, period_label, kind).name}"

    def _open_tile_action(self, tile_labels: list[str], action_labels: list[str]) -> bool:
        """Click an action button inside a Returns Dashboard tile."""
        tile = self._tile(tile_labels)
        if tile is None:
            return False
        if not self._click_text(action_labels, tile):
            return False
        time.sleep(4)
        self.dismiss_post_login_prompts(timeout=2)
        return True

    def _has_button(self, labels: list[str]) -> bool:
        """Report whether a button is on the page without clicking it."""
        from selenium.webdriver.common.by import By

        upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        lower = "abcdefghijklmnopqrstuvwxyz"
        for label in labels:
            xpath = (
                "//*[self::button or self::a or @role='button']"
                f"[contains(translate(normalize-space(.),'{upper}','{lower}'),'{label.lower()}')]"
            )
            for element in self.driver.find_elements(By.XPATH, xpath):
                if element.is_displayed() and element.is_enabled():
                    return True
        return False

    def _back_until(self, labels: list[str], attempts: int = 3) -> bool:
        """Step back with the portal's BACK control until a page with ``labels`` shows.

        GSTN answers "Access Denied" to direct navigation of authenticated
        routes even in a logged-in browser, so returning to a previous page is
        only ever done through BACK, never by loading its URL.
        """
        for _ in range(attempts):
            if self._has_button(labels):
                return True
            if not self._click_exact_button(["back"]):
                self.driver.back()
            time.sleep(4)
            self.dismiss_post_login_prompts(timeout=2)
        return self._has_button(labels)

    def _scroll_to_bottom(self) -> None:
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)

    # ------------------------------------------------------------------
    # Per-report download flows
    # ------------------------------------------------------------------
    GSTR1_TILE = ["details of outward supplies", "gstr-1"]
    GSTR3B_TILE = ["monthly return gstr-3b", "monthly return gstr3b", "gstr-3b"]
    GSTR2B_TILE = ["auto-drafted itc statement", "gstr-2b"]

    E_INVOICE_LABELS = ["download details from e-invoices (excel)",
                        "download details from e-invoice (excel)",
                        "download e-invoice details", "e-invoice download",
                        "download details from e-invoices"]

    def _download_gstr1_group(self, period_label: str, folders: dict[str, Path],
                              results_url: str) -> list[str]:
        """Walk the GSTR-1 tile the way the portal is navigated by hand.

        VIEW opens the return, scrolling down reveals VIEW INVOICES where the
        e-invoice Excel is taken, and coming back to that same page VIEW
        SUMMARY leads to the summary PDF.
        """
        messages: list[str] = []

        # 1. VIEW on the tile opens the GSTR-1 return.
        if not self._open_tile_action(self.GSTR1_TILE, ["view"]):
            return [f"E-Invoice {period_label}: GSTR-1 VIEW action not available",
                    f"GSTR-1 PDF {period_label}: GSTR-1 VIEW action not available"]

        # 2. Scroll down and open VIEW INVOICES, which holds the e-invoice export.
        self._scroll_to_bottom()
        opened_invoices = self._wait_and_click_text(
            ["view invoices", "view invoice", "e-invoices", "e-invoice"], timeout=15)
        if opened_invoices:
            time.sleep(3)
            self.dismiss_post_login_prompts(timeout=2)
        messages.append(self._download_here(
            "E-Invoice", period_label, folders["E-Invoice"],
            self.E_INVOICE_LABELS, timeout=90,
        ))

        # 3. Back on the GSTR-1 page, VIEW SUMMARY opens the page with the PDF.
        if opened_invoices:
            self._back_until(["view summary"])
        if self._click_exact_button(["view summary"]) or self._wait_and_click_text(
                ["view summary"], timeout=10):
            time.sleep(4)
            self.dismiss_post_login_prompts(timeout=2)
        messages.append(self._download_here(
            "GSTR-1", period_label, folders["GSTR-1"],
            ["download summary (pdf)", "download (pdf)", "download pdf",
             "download summary", "preview gstr-1 (pdf)"],
            kind="pdf", timeout=90,
        ))
        self._return_to_monthly_tiles(results_url)
        return messages

    GSTR3B_DOWNLOAD_LABELS = ["download filed gstr-3b (pdf)", "download filed gstr-3b",
                              "download filed gstr3b", "generate pdf file to download",
                              "generate excel file to download", "generate file to download",
                              "download gstr-3b (pdf)", "download (pdf)", "download pdf"]

    def _download_gstr3b(self, period_label: str, folder: Path, results_url: str) -> list[str]:
        """GSTR-3B tile: DOWNLOAD, then the generate control on the download page.

        The PDF spellings are listed first because the reconciliation workbook
        reads the filed PDF — GSTN publishes no machine-readable filed GSTR-3B.
        """
        if not self._open_tile_action(self.GSTR3B_TILE, ["download"]):
            return [f"GSTR-3B {period_label}: DOWNLOAD action not available on the tile"]
        message = self._download_here(
            "GSTR-3B", period_label, folder, self.GSTR3B_DOWNLOAD_LABELS, generated=True,
        )
        self._return_to_monthly_tiles(results_url)
        return [message]

    def _download_gstr2b(self, period_label: str, folder: Path, results_url: str) -> list[str]:
        """GSTR-2B tile: DOWNLOAD → GENERATE EXCEL FILE TO DOWNLOAD."""
        if not self._open_tile_action(self.GSTR2B_TILE, ["download"]):
            return [f"GSTR-2B {period_label}: DOWNLOAD action not available on the tile"]
        message = self._download_here(
            "GSTR-2B", period_label, folder,
            ["generate excel file to download", "download gstr-2b details (excel)",
             "download excel", "generate excel"],
            kind="excel", generated=True,
        )
        self._return_to_monthly_tiles(results_url)
        return [message]

    def download_financial_year(self, credential: GstCredential, financial_year: str,
                                progress: Callable[[int, str], None] | None = None) -> dict[str, object]:
        """Download all available monthly GST returns after the user has logged in."""
        if not self.is_logged_in():
            raise RuntimeError("Complete CAPTCHA/OTP and click Login before starting automatic downloads.")
        root = self.resolve_client_root(self.download_dir, credential.label, financial_year)
        report_folders = {name: root / name for name in REPORT_FOLDERS}
        for folder in report_folders.values():
            folder.mkdir(parents=True, exist_ok=True)
        results: list[str] = []
        periods = financial_year_periods(financial_year)
        steps_per_period = 4
        total = len(periods) * steps_per_period
        completed = 0

        def report(message: str, count: int = 1) -> None:
            nonlocal completed
            completed += count
            if progress:
                progress(min(100, int(completed * 100 / total)), message)

        for month, quarter, period_label in periods:
            try:
                if progress:
                    progress(min(100, int(completed * 100 / total)),
                             f"{period_label}: selecting FY, Quarter {quarter}, {month}, then SEARCH…")
                self._prepare_period(financial_year, quarter, month)
            except Exception as exc:
                message = f"{period_label}: dashboard preparation failed: {exc}"
                results.extend([message] * steps_per_period)
                report(message, steps_per_period)
                continue

            jobs = (
                ("GSTR-1 / E-Invoice", 2,
                 lambda label, url: self._download_gstr1_group(label, report_folders, url)),
                ("GSTR-3B", 1,
                 lambda label, url: self._download_gstr3b(label, report_folders["GSTR-3B"], url)),
                ("GSTR-2B", 1,
                 lambda label, url: self._download_gstr2b(label, report_folders["GSTR-2B"], url)),
            )
            for name, weight, runner in jobs:
                if progress:
                    progress(min(100, int(completed * 100 / total)),
                             f"{name} {period_label}: opening the report and downloading…")
                try:
                    if self._tile(self.GSTR1_TILE) is None and self._tile(self.GSTR3B_TILE) is None:
                        self._prepare_period(financial_year, quarter, month)
                    messages = runner(period_label, self.driver.current_url)
                except Exception as exc:
                    messages = [f"{name} {period_label}: download failed: {exc}"]
                results.extend(messages)
                report(messages[-1], weight)

        manifest = root / "download_status.txt"
        # Append so a re-run that fills gaps keeps the earlier history.
        stamp = f"# run {datetime.now():%Y-%m-%d %H:%M:%S} — {financial_year}"
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write("\n".join([stamp, *results]) + "\n")
        logged_out = self.logout()
        return {
            "root": str(root),
            "folders": {key: str(value) for key, value in report_folders.items()},
            "results": results,
            "manifest": str(manifest),
            "logged_out": logged_out,
        }

    @classmethod
    def resolve_client_root(cls, base: str | Path, client: str, financial_year: str) -> Path:
        """Return ``<base>/<client>/<financial year>`` without re-nesting on re-runs.

        The user can point the tool at the base download folder or at a folder a
        previous run already created. Any client/financial-year/report segments
        already at the end of the path are peeled off first, so repeated runs
        update the same folders instead of burying new ones inside them.
        """
        root = Path(base).resolve()
        safe = cls._safe_name(client)
        while root.parent != root:
            name = root.name
            if name in REPORT_FOLDERS or name == safe or cls._is_financial_year(name):
                root = root.parent
                continue
            break
        return root / safe / financial_year

    @staticmethod
    def _is_financial_year(name: str) -> bool:
        return bool(re.fullmatch(r"\d{4}-\d{2}|\d{4}-\d{4}", name.strip()))

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
        return safe or "GST_Client"

    def close(self) -> None:
        if self.driver is not None:
            self.driver.quit()
            self.driver = None

    def logout(self) -> bool:
        if self.driver is None:
            return False
        try:
            self.driver.get(GST_LOGOUT_URL)
            time.sleep(3)
            return "login" in (self.driver.current_url or "").lower()
        except Exception:
            LOG.exception("GST logout failed")
            return False
