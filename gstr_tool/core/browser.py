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


class GstBrowserSession:
    """Selenium-backed, human-in-the-loop GST Portal session.

    CAPTCHA, OTP and the final Login click remain with the user. Selectors and
    navigation are isolated here because the GST Portal can change its UI.
    """

    def __init__(self, download_dir: str | Path):
        self.download_dir = Path(download_dir).resolve()
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

    def run_in_background(self) -> None:
        """Keep Chrome compact but rendered so GST JavaScript is not suspended."""
        if self.driver is not None:
            try:
                width = 520
                height = 640
                screen_width = self.driver.execute_script("return screen.availWidth;") or 1280
                self.driver.set_window_size(width, height)
                self.driver.set_window_position(max(0, int(screen_width) - width), 0)
            except Exception:
                self.driver.set_window_size(520, 640)

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

    def _download_from_detail(self, report: str, period_label: str, folder: Path,
                              tile_labels: list[str], view_labels: list[str],
                              download_labels: list[str], close_summary: bool = False) -> str:
        """Open a return's VIEW page and click its report-specific download button."""
        self._set_download_directory(folder)
        before = self._snapshot(folder)
        tile = self._tile(tile_labels)
        if tile is None:
            return f"{report} {period_label}: tile not available"
        if not self._click_text(view_labels, tile):
            return f"{report} {period_label}: VIEW action not available"
        time.sleep(4)
        self.dismiss_post_login_prompts(timeout=2)
        if close_summary:
            self._wait_and_click_text(["close"], timeout=8)
        return self._download_open_detail(report, period_label, folder, download_labels)

    def _download_open_detail(self, report: str, period_label: str, folder: Path,
                              download_labels: list[str]) -> str:
        """Download one file from a return detail page that is already open."""
        self._set_download_directory(folder)
        before = self._snapshot(folder)
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        if not self._wait_and_click_text(download_labels, timeout=25):
            return f"{report} {period_label}: detail-page download action not available"
        downloaded = self._wait_for_download(folder, before, timeout=45)
        if downloaded is None:
            return f"{report} {period_label}: download did not arrive within 45 seconds"
        safe_report = report.replace("-", "")
        renamed = downloaded.with_name(f"{period_label}_{safe_report}_{downloaded.name}")
        if renamed != downloaded and not renamed.exists():
            downloaded.rename(renamed)
            downloaded = renamed
        return f"{report} {period_label}: {downloaded.name}"

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

    def _download_report(self, report: str, period_label: str, folder: Path,
                         tile_labels: list[str], action_labels: list[str]) -> str:
        self._set_download_directory(folder)
        before = self._snapshot(folder)
        tile = self._tile(tile_labels)
        if tile is None:
            return f"{report} {period_label}: tile not available"
        if not self._click_text(action_labels, tile):
            return f"{report} {period_label}: download action not available"
        time.sleep(2)
        # GST often opens a modal/menu after the first Download click.
        self._wait_and_click_text(
            ["download json", "generate json", "download excel", "download filed", "download pdf"],
            timeout=15,
        )
        # A generated file may appear as a separate link after GSTN processes it.
        self._wait_and_click_text(["click here to download", "download generated file"], timeout=15)
        downloaded = self._wait_for_download(folder, before)
        if downloaded is None:
            return f"{report} {period_label}: request submitted; file not ready within 120 seconds"
        safe_report = report.replace("-", "")
        renamed = downloaded.with_name(f"{period_label}_{safe_report}_{downloaded.name}")
        if renamed != downloaded and not renamed.exists():
            downloaded.rename(renamed)
            downloaded = renamed
        return f"{report} {period_label}: {downloaded.name}"

    def download_financial_year(self, credential: GstCredential, financial_year: str,
                                progress: Callable[[int, str], None] | None = None) -> dict[str, object]:
        """Download all available monthly GST returns after the user has logged in."""
        if not self.is_logged_in():
            raise RuntimeError("Complete CAPTCHA/OTP and click Login before starting automatic downloads.")
        root = self.download_dir / self._safe_name(credential.label) / financial_year
        report_folders = {name: root / name for name in ("GSTR-1", "GSTR-3B", "GSTR-2B", "E-Invoice")}
        for folder in report_folders.values():
            folder.mkdir(parents=True, exist_ok=True)
        results: list[str] = []
        periods = financial_year_periods(financial_year)
        total = len(periods) * 4
        completed = 0
        jobs = (
            ("GSTR-3B", ["monthly return gstr-3b", "gstr-3b"], ["view gstr3b", "view"],
             ["download filed gstr-3b"], True),
            ("GSTR-2B", ["auto-drafted itc statement", "gstr-2b"], ["view"],
             ["download gstr-2b details (excel)"], False),
        )
        for month, quarter, period_label in periods:
            try:
                if progress:
                    progress(int(completed * 100 / total),
                             f"{period_label}: selecting FY, Quarter {quarter}, {month}, then SEARCH…")
                self._prepare_period(financial_year, quarter, month)
            except Exception as exc:
                message = f"{period_label}: dashboard preparation failed: {exc}"
                results.extend([message] * 4)
                completed += 4
                if progress:
                    progress(int(completed * 100 / total), message)
                continue

            # GSTR-1 requires two levels: VIEW opens its details dashboard,
            # where e-invoice Excel is downloaded; VIEW SUMMARY then opens the
            # summary page containing DOWNLOAD(PDF).
            gstr1_tile_labels = ["details of outward supplies", "gstr-1"]
            try:
                if progress:
                    progress(int(completed * 100 / total),
                             f"GSTR-1 {period_label}: clicking VIEW and opening the summary…")
                results_url = self.driver.current_url
                tile = self._tile(gstr1_tile_labels)
                if tile is None or not self._click_text(["view summary", "view"], tile):
                    raise RuntimeError("GSTR-1 VIEW action was not found")
                time.sleep(4)
                self.dismiss_post_login_prompts(timeout=2)
                if progress:
                    progress(int(completed * 100 / total),
                             f"E-Invoice {period_label}: downloading Excel before summary…")
                message = self._download_open_detail(
                    "E-Invoice", period_label, report_folders["E-Invoice"],
                    ["download details from e-invoices (excel)",
                     "download details from e-invoice (excel)"],
                )
                results.append(message)
                completed += 1
                if progress:
                    progress(int(completed * 100 / total), message)

                if progress:
                    progress(int(completed * 100 / total),
                             f"GSTR-1 {period_label}: clicking VIEW SUMMARY…")
                if not self._click_exact_button(["view summary"]):
                    message = f"GSTR-1 {period_label}: VIEW SUMMARY action not available"
                else:
                    time.sleep(4)
                    message = self._download_open_detail(
                        "GSTR-1", period_label, report_folders["GSTR-1"],
                        ["download(pdf)", "download (pdf)"],
                    )
                results.append(message)
                completed += 1
                if progress:
                    progress(int(completed * 100 / total), message)
                self._return_to_monthly_tiles(results_url)
            except Exception as exc:
                message = f"GSTR-1/E-Invoice {period_label}: download failed: {exc}"
                results.extend([message, message])
                completed += 2
                if progress:
                    progress(int(completed * 100 / total), message)

            for report, tile_labels, view_labels, download_labels, close_summary in jobs:
                try:
                    if progress:
                        progress(int(completed * 100 / total),
                                 f"{report} {period_label}: opening report, scrolling down and downloading…")
                    if self._tile(tile_labels) is None:
                        self._prepare_period(financial_year, quarter, month)
                    results_url = self.driver.current_url
                    message = self._download_from_detail(
                        report, period_label, report_folders[report], tile_labels,
                        view_labels, download_labels, close_summary,
                    )
                    self._return_to_monthly_tiles(results_url)
                except Exception as exc:
                    message = f"{report} {period_label}: dashboard/download failed: {exc}"
                results.append(message)
                completed += 1
                if progress:
                    progress(int(completed * 100 / total), message)
        manifest = root / "download_status.txt"
        manifest.write_text("\n".join(results) + "\n", encoding="utf-8")
        logged_out = self.logout()
        return {
            "root": str(root),
            "folders": {key: str(value) for key, value in report_folders.items()},
            "results": results,
            "manifest": str(manifest),
            "logged_out": logged_out,
        }

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
