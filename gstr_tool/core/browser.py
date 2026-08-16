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
RETURNS_DASHBOARD_URL = "https://return.gst.gov.in/returns/auth/dashboard"


def financial_year_periods(financial_year: str) -> list[tuple[str, str]]:
    """Return (portal period code, readable folder/file prefix) for Apr-Mar."""
    match = re.fullmatch(r"(\d{4})-(\d{2}|\d{4})", financial_year.strip())
    if not match:
        raise ValueError("Financial year must look like 2025-26.")
    start = int(match.group(1))
    end = start + 1
    return [
        (f"{month:02d}{start}", datetime(start, month, 1).strftime("%b-%Y"))
        for month in range(4, 13)
    ] + [
        (f"{month:02d}{end}", datetime(end, month, 1).strftime("%b-%Y"))
        for month in range(1, 4)
    ]


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
        self.dismiss_post_login_prompts()
        self.driver.get(RETURNS_DASHBOARD_URL)
        time.sleep(2)
        self.dismiss_post_login_prompts()

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
        """Minimize the automated Chrome window after manual login is complete."""
        if self.driver is not None:
            try:
                self.driver.minimize_window()
            except Exception:
                self.driver.set_window_position(-32000, -32000)

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
                    attempts.extend((lambda: selector.select_by_visible_text(text),
                                     lambda: selector.select_by_visible_text(text.replace("-", " - "))))
                if value:
                    attempts.append(lambda: selector.select_by_value(value))
                for attempt in attempts:
                    try:
                        attempt(); return
                    except Exception:  # try the next portal representation
                        continue
        raise RuntimeError(f"GST Portal selection was not found: {text or value}")

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
                    element.click()
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

    def _tile(self, labels: list[str]):
        from selenium.webdriver.common.by import By

        for label in labels:
            needle = label.lower()
            xpath = (
                "//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
                f"'{needle}')]/ancestor::div[contains(@class,'card') or contains(@class,'tile') or contains(@class,'panel')][1]"
            )
            visible = [element for element in self.driver.find_elements(By.XPATH, xpath) if element.is_displayed()]
            if visible:
                return visible[0]
        return None

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
        total = len(periods) * 3
        completed = 0
        for period_code, period_label in periods:
            try:
                self.driver.get(RETURNS_DASHBOARD_URL)
                time.sleep(2)
                self.dismiss_post_login_prompts()
                self._select([
                    "//select[contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'fin')]",
                    "//select[contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'fin')]",
                    "//label[contains(.,'Financial Year')]/following::select[1]",
                ], text=financial_year)
                self._select([
                    "//select[contains(translate(@id,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'period')]",
                    "//select[contains(translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'period')]",
                    "//label[contains(.,'Return Filing Period')]/following::select[1]",
                ], value=period_code)
                if not self._click_text(["search"]):
                    raise RuntimeError("GST Portal SEARCH button was not found.")
                time.sleep(3)
                self.dismiss_post_login_prompts()
            except Exception as exc:
                message = f"{period_label}: dashboard preparation failed: {exc}"
                results.append(message)
                completed += 3
                if progress:
                    progress(int(completed * 100 / total), message)
                continue
            jobs = (
                ("GSTR-1", ["gstr-1", "gstr 1"], ["download", "view filed"]),
                ("GSTR-3B", ["gstr-3b", "gstr 3b"], ["download", "view filed"]),
                ("GSTR-2B", ["gstr-2b", "gstr 2b", "auto-drafted itc"], ["download"]),
            )
            for report, tile_labels, actions in jobs:
                message = self._download_report(report, period_label, report_folders[report], tile_labels, actions)
                results.append(message)
                completed += 1
                if progress:
                    progress(int(completed * 100 / total), message)
        results.append("E-Invoice: separate portal authentication is required; folder prepared only.")
        manifest = root / "download_status.txt"
        manifest.write_text("\n".join(results) + "\n", encoding="utf-8")
        return {
            "root": str(root),
            "folders": {key: str(value) for key, value in report_folders.items()},
            "results": results,
            "manifest": str(manifest),
        }

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
        return safe or "GST_Client"

    def close(self) -> None:
        if self.driver is not None:
            self.driver.quit()
            self.driver = None
