from __future__ import annotations

import logging
from pathlib import Path

from .models import GstCredential


LOG = logging.getLogger(__name__)
GST_LOGIN_URL = "https://services.gst.gov.in/services/login"


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
        self.driver.get("https://return.gst.gov.in/returns/auth/dashboard")

    def close(self) -> None:
        if self.driver is not None:
            self.driver.quit()
            self.driver = None
