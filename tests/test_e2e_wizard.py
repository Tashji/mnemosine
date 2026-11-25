"""Test end-to-end del primo avvio con un browser reale.

Il test compie il percorso che segue chi clona il progetto: apre ``/setup``,
compila i campi obbligatori, attraversa i sei passaggi, crea il profilo senza
biografia né indice (per non scaricare il modello) e verifica che la dashboard
si apra. Usa Chromium di Playwright; se il browser manca, il test viene
saltato invece di fallire.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_memory.dashboard import create_server

try:  # pragma: no cover - dipende dall'ambiente
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
    PlaywrightError = Exception


@unittest.skipUnless(sync_playwright is not None, "Playwright non installato")
class SetupWizardE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        self.server = create_server(
            self.root,
            host="127.0.0.1",
            port=0,
            quiet=True,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def test_first_run_creates_profile_and_opens_dashboard(self) -> None:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.goto(self.base_url + "/setup")
                    page.fill('input[name="first_name"]', "Elena")
                    page.fill('input[name="last_name"]', "Prova")
                    page.fill('input[name="birth_date"]', "1990-05-12")
                    page.fill('input[name="birth_city"]', "Bologna")

                    for step in range(2, 7):
                        page.click("#wizard-next")
                        page.wait_for_selector(
                            f'[data-step="{step}"]:not([hidden])',
                            timeout=15_000,
                        )

                    page.uncheck('input[name="generate_biography"]')
                    page.uncheck('input[name="build_index"]')
                    page.click("#wizard-submit")

                    page.wait_for_url("**/setup/finalize", timeout=60_000)
                    page.wait_for_selector("text=Profilo creato")
                    page.click('a:has-text("Apri la dashboard")')

                    page.wait_for_selector("#view-overview:not([hidden])")
                    self.assertEqual(
                        page.text_content("#page-title").strip(), "Panoramica"
                    )
                    self.assertTrue(
                        page.is_visible('button[data-view="maintenance"]')
                    )
                    self.assertTrue((self.root / "config" / "identity.json").is_file())
                finally:
                    browser.close()
        except PlaywrightError as exc:  # pragma: no cover
            self.skipTest(f"Chromium non disponibile: {exc}")


if __name__ == "__main__":
    unittest.main()
