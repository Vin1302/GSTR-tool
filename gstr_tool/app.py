from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QProgressBar, QVBoxLayout, QWidget,
)

from .core.browser import GstBrowserSession
from .core.credentials import load_credentials
from .core.models import GstCredential
from .core.pipeline import run


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class PipelineWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, template: str, downloads: str, output: str):
        super().__init__()
        self.args = (template, downloads, output)

    def start(self):
        try:
            self.finished.emit(run(*self.args))
        except Exception as exc:  # noqa: BLE001
            logging.exception("GSTR processing failed")
            self.failed.emit(str(exc))


class DownloadWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, session, credential, financial_year):
        super().__init__()
        self.session = session
        self.credential = credential
        self.financial_year = financial_year

    def start(self):
        try:
            result = self.session.download_financial_year(
                self.credential,
                self.financial_year,
                progress=lambda percent, message: self.progress.emit(percent, message),
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Automatic GST download failed")
            self.failed.emit(str(exc))


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GSTR Tool")
        self.setMinimumWidth(720)
        self.credentials: list[GstCredential] = []
        self.browser_session: GstBrowserSession | None = None
        self.thread = None
        self.worker = None
        self._download_active = False
        self.login_watcher = QTimer(self)
        self.login_watcher.setInterval(1500)
        self.login_watcher.timeout.connect(self._poll_login)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        title = QLabel("GSTR Download & Reconciliation Tool")
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        layout.addWidget(title)
        note = QLabel("Select a credential workbook, choose a client, and open GST login. Complete CAPTCHA and click Login yourself. Then download the return files and generate the formula-preserving template.")
        note.setWordWrap(True)
        layout.addWidget(note)

        login_box = QGroupBox("1. GST login")
        login_form = QFormLayout(login_box)
        self.credentials_path = QLineEdit()
        self.credentials_path.setReadOnly(True)
        choose_credentials = QPushButton("Choose credential Excel…")
        choose_credentials.clicked.connect(self.choose_credentials)
        credential_row = QHBoxLayout(); credential_row.addWidget(self.credentials_path); credential_row.addWidget(choose_credentials)
        login_form.addRow("Credential file:", credential_row)
        self.client_combo = QComboBox()
        login_form.addRow("Client:", self.client_combo)
        self.open_login_button = QPushButton("Open GST login for selected client")
        self.open_login_button.clicked.connect(self.open_login)
        login_form.addRow(self.open_login_button)
        self.dashboard_button = QPushButton("Login completed — open Returns Dashboard")
        self.dashboard_button.clicked.connect(self.open_dashboard)
        login_form.addRow(self.dashboard_button)
        self.auto_download_checkbox = QCheckBox("Start downloads automatically after successful login")
        self.auto_download_checkbox.setChecked(True)
        login_form.addRow(self.auto_download_checkbox)
        self.background_checkbox = QCheckBox("Minimize Chrome while automatic downloads run")
        self.background_checkbox.setChecked(False)
        login_form.addRow(self.background_checkbox)
        self.financial_year_combo = QComboBox()
        current_start = date.today().year if date.today().month >= 4 else date.today().year - 1
        for start_year in range(current_start, 2016, -1):
            self.financial_year_combo.addItem(f"{start_year}-{str(start_year + 1)[-2:]}")
        login_form.addRow("Financial year:", self.financial_year_combo)
        self.download_all_button = QPushButton("Download complete selected financial year")
        self.download_all_button.setMinimumHeight(40)
        self.download_all_button.clicked.connect(self.download_financial_year)
        login_form.addRow(self.download_all_button)
        layout.addWidget(login_box)

        process_box = QGroupBox("2. Build reconciliation workbook")
        form = QFormLayout(process_box)
        self.template_path = self._path_row(form, "Template:", "Choose template…", self.choose_template)
        self.download_path = self._path_row(form, "GST downloads:", "Choose folder…", self.choose_downloads)
        self.output_path = self._path_row(form, "Output folder:", "Choose folder…", self.choose_output)
        self.generate_button = QPushButton("Generate GSTR workbook")
        self.generate_button.setMinimumHeight(42)
        self.generate_button.clicked.connect(self.generate)
        form.addRow(self.generate_button)
        layout.addWidget(process_box)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.hide(); layout.addWidget(self.progress)
        self.status = QLabel(""); self.status.setWordWrap(True); layout.addWidget(self.status)

    def _path_row(self, form, label, button_text, callback):
        edit = QLineEdit(); edit.setReadOnly(True)
        button = QPushButton(button_text); button.clicked.connect(callback)
        row = QHBoxLayout(); row.addWidget(edit); row.addWidget(button)
        form.addRow(label, row)
        return edit

    def choose_credentials(self):
        path, _ = QFileDialog.getOpenFileName(self, "Credential workbook", "", "Excel (*.xlsx *.xlsm)")
        if not path: return
        try:
            self.credentials = load_credentials(path)
        except Exception as exc:
            QMessageBox.critical(self, "Credential file error", str(exc)); return
        self.credentials_path.setText(path)
        self.client_combo.clear()
        self.client_combo.addItems([item.label for item in self.credentials])
        self.status.setText(f"Loaded {len(self.credentials)} client login(s). Passwords are held only in memory.")

    def choose_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "GSTR template", "", "Excel (*.xlsx *.xlsm)")
        if path: self.template_path.setText(path)

    def choose_downloads(self):
        path = QFileDialog.getExistingDirectory(self, "GST download folder")
        if path: self.download_path.setText(path)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Output folder")
        if path: self.output_path.setText(path)

    def open_login(self):
        if not self.credentials:
            QMessageBox.information(self, "Credentials needed", "Choose the credential workbook first."); return
        if not self.download_path.text():
            self.choose_downloads()
            if not self.download_path.text(): return
        try:
            if self.browser_session: self.browser_session.close()
            self.browser_session = GstBrowserSession(self.download_path.text())
            self.browser_session.open_login(self.credentials[self.client_combo.currentIndex()])
            self.login_watcher.start()
            self.status.setText("GST login is ready. Complete CAPTCHA/OTP and click Login. The app will dismiss optional reminders and start automatically.")
        except Exception as exc:
            QMessageBox.critical(self, "Browser error", str(exc))

    def open_dashboard(self):
        try:
            if not self.browser_session: raise RuntimeError("Open GST login first.")
            self.browser_session.open_returns_dashboard()
            self.status.setText("Returns Dashboard opened. Select the FY/period and download GSTR-1, GSTR-3B and GSTR-2B; download valid e-Invoices from the e-Invoice portal into the same folder.")
        except Exception as exc:
            QMessageBox.warning(self, "Login not complete", str(exc))

    def _poll_login(self):
        if not self.browser_session or self._download_active:
            return
        try:
            if not self.browser_session.is_logged_in():
                return
            self.login_watcher.stop()
            dismissed = self.browser_session.dismiss_post_login_prompts()
            if dismissed:
                self.status.setText(f"Login detected; dismissed {len(dismissed)} optional GST reminder(s).")
            if self.auto_download_checkbox.isChecked():
                self.download_financial_year()
        except Exception as exc:
            self.login_watcher.stop()
            self.status.setText(f"Login was detected, but GST post-login preparation failed: {exc}")

    def download_financial_year(self):
        if not self.browser_session:
            QMessageBox.information(self, "Login needed", "Open GST login and complete CAPTCHA/OTP first."); return
        if not self.browser_session.is_logged_in():
            QMessageBox.information(self, "Login not complete", "Complete CAPTCHA/OTP and click Login in Chrome first."); return
        credential = self.credentials[self.client_combo.currentIndex()]
        financial_year = self.financial_year_combo.currentText()
        self._download_active = True
        self.login_watcher.stop()
        try:
            self.browser_session.dismiss_post_login_prompts()
            if self.background_checkbox.isChecked():
                self.browser_session.run_in_background()
        except Exception as exc:
            self._download_active = False
            QMessageBox.warning(self, "Browser preparation failed", str(exc)); return
        self.download_all_button.setEnabled(False)
        self.progress.setRange(0, 100); self.progress.setValue(0); self.progress.show()
        self.status.setText(f"Downloading {financial_year} returns for {credential.label}…")
        self.thread = QThread()
        self.worker = DownloadWorker(self.browser_session, credential, financial_year)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.progress.connect(self.download_progress)
        self.worker.finished.connect(self.download_done)
        self.worker.failed.connect(self.download_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def download_progress(self, percent, message):
        self.progress.setValue(percent)
        self.status.setText(message)

    def download_done(self, result):
        self._download_active = False
        self.progress.hide(); self.download_all_button.setEnabled(True)
        self.download_path.setText(result["root"])
        unavailable = [message for message in result["results"] if "not available" in message or "not ready" in message]
        message = f"Automatic GST download completed. Files are separated under {result['root']}."
        if unavailable:
            message += f" {len(unavailable)} period/report downloads were unavailable or still being generated; review the status messages and retry if needed."
        self.status.setText(message)
        QMessageBox.information(self, "Financial-year download completed", message)

    def download_failed(self, message):
        self._download_active = False
        self.progress.hide(); self.download_all_button.setEnabled(True)
        if self.browser_session:
            self.browser_session.restore_browser()
        self.status.setText(message)
        QMessageBox.critical(self, "Automatic download failed", message)

    def generate(self):
        fields = [self.template_path.text(), self.download_path.text(), self.output_path.text()]
        if not all(fields):
            QMessageBox.information(self, "Files needed", "Choose the template, GST download folder, and output folder."); return
        self.progress.setRange(0, 0); self.progress.show(); self.generate_button.setEnabled(False); self.status.setText("Reading GST downloads and preserving template formulas…")
        self.thread = QThread(); self.worker = PipelineWorker(*fields); self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start); self.worker.finished.connect(self.done); self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.thread.quit); self.worker.failed.connect(self.thread.quit); self.thread.start()

    def done(self, result):
        self.progress.hide(); self.generate_button.setEnabled(True)
        self.status.setText(f"Created {result['output']} from {len(result['files'])} file(s); {result['invoice_count']} eligible 2B invoice rows included.")
        QMessageBox.information(self, "Workbook created", self.status.text())

    def failed(self, message):
        self.progress.hide(); self.generate_button.setEnabled(True); self.status.setText(message)
        QMessageBox.critical(self, "Generation failed", message)

    def closeEvent(self, event):
        if self.browser_session: self.browser_session.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow(); window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
