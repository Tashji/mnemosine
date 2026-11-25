"""Test end-to-end della dashboard con un browser reale.

Il test copre il percorso amministrativo completo della vista **Incontrati**:
accesso, coda di revisione, conferma di un fatto, promozione a relazione e
verifica del contatto nella vista **Relazioni**. Usa Chromium di Playwright;
se il browser non è installato il test viene saltato, non fallisce.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_memory.acquaintances import AcquaintanceService
from agent_memory.dashboard import create_server
from agent_memory.dashboard_auth import DashboardAuth
from tests.support import copy_profile

try:  # pragma: no cover - dipende dall'ambiente
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
    PlaywrightError = Exception

PASSWORD = "correct horse battery"


@unittest.skipUnless(
    sync_playwright is not None, "Playwright non installato"
)
class DashboardE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        copy_profile(self.root)
        service = AcquaintanceService(self.root)
        person = service.store.create("Nina", channel="chat", handle="nina")
        service.store.observe(
            person_id=person["person_id"],
            category="city",
            value="Bologna",
            source="thread presentazioni",
        )
        self.server = create_server(
            self.root,
            host="127.0.0.1",
            port=0,
            quiet=True,
            auth=DashboardAuth(PASSWORD),
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

    def test_incontrati_review_and_promotion_flow(self) -> None:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.goto(self.base_url + "/login")
                    page.fill('input[name="password"]', PASSWORD)
                    page.click('button[type="submit"]')
                    page.wait_for_url(self.base_url + "/")

                    page.click('button[data-view="acquaintances"]')
                    page.wait_for_selector("#view-acquaintances:not([hidden])")
                    page.wait_for_selector("text=Da rivedere")
                    review = page.text_content(".acquaintance-review-item")
                    self.assertIn("Bologna", review)
                    self.assertEqual(
                        page.get_attribute(
                            'a[href="/api/acquaintances/export"]', "download"
                        ),
                        "incontrati.json",
                    )

                    page.click(
                        '.acquaintance-review-item button:has-text("Conferma")'
                    )
                    page.wait_for_selector("text=Fatto confermato.")
                    page.wait_for_selector(
                        ".acquaintance-review", state="detached"
                    )

                    page.click('summary:has-text("Promuovi a relazione")')
                    edit = page.locator(
                        "details.acquaintance-edit",
                        has_text="Promuovi a relazione",
                    )
                    edit.locator('input[name="relation"]').fill("Amica")
                    edit.locator('input[name="trust"]').fill("55")
                    edit.locator('input[name="intimacy"]').fill("20")
                    edit.locator('input[name="confirmation"]').fill("PROMUOVI")
                    edit.locator(
                        'button:has-text("Promuovi a relazione")'
                    ).click()
                    page.wait_for_selector("text=è ora una relazione fidata")

                    page.click('button[data-view="relations"]')
                    page.wait_for_selector("#view-relations:not([hidden])")
                    page.wait_for_selector(
                        "#relations-content .relation-select-name:has-text('Nina')"
                    )
                    page.wait_for_selector(
                        "#relations-content .relation-select-relation:has-text('Amica')"
                    )
                finally:
                    browser.close()
        except PlaywrightError as exc:  # pragma: no cover
            self.skipTest(f"Chromium non disponibile: {exc}")


if __name__ == "__main__":
    unittest.main()
