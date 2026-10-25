"""Test della prima configurazione del profilo dalla dashboard."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from agent_memory.dashboard import DashboardData, create_server
from agent_memory.dashboard_auth import DashboardAuth
from agent_memory.profile import (
    PROFILE_STATE_INCOMPLETE,
    PROFILE_STATE_MISSING,
    PROFILE_STATE_READY,
    profile_state,
)

PASSWORD = "correct horse battery"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SetupFixture(unittest.TestCase):
    auth: DashboardAuth | None = None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        self.server = create_server(
            self.root,
            host="127.0.0.1",
            port=0,
            quiet=True,
            auth=self.auth,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(NoRedirect)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def _request(
        self,
        path: str,
        *,
        data: bytes | None = None,
        cookie: str | None = None,
        origin: str | None = None,
    ):
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method="POST" if data is not None else "GET",
        )
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        self.addCleanup(response.close)
        return response

    def _csrf(self, path: str) -> tuple[str, str]:
        response = self._request(path)
        self.assertEqual(response.status, 200)
        page = response.read().decode("utf-8")
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        self.assertIsNotNone(match)
        return page, match.group(1)

    def _form(self, **overrides: str) -> dict[str, str]:
        values = {
            "first_name": "Giulia",
            "last_name": "Bianchi",
            "birth_date": "1998-04-12",
            "birth_city": "Milano",
            "birth_province": "MI",
            "birth_region": "Lombardia",
            "birth_country": "Italia",
            "birth_country_code": "IT",
            "citizenship": "italiana",
            "residence_city": "Torino",
            "residence_province": "TO",
            "residence_region": "Piemonte",
            "residence_country": "Italia",
            "residence_since": "2018-09-01",
            "timezone": "Europe/Rome",
            "gender": "femminile",
            "pronouns": "lei",
            "language_0_code": "it",
            "language_0_name": "italiano",
            "language_0_level": "madrelingua",
            "language_1_code": "en",
            "language_1_name": "inglese",
            "language_1_level": "B2",
            "trait_introversion_extraversion": "3",
            "trait_empathy_detachment": "7",
            "trait_logic_impulsivity": "4",
            "trait_resilience_sensitivity": "8",
            "trait_introversion_extraversion_note": "Preferisce pochi legami profondi.",
            "interests": "fotografia, cinema",
            "values": "onestà\nlealtà",
            "habits": "legge la sera",
            "parent_0_first_name": "Paolo",
            "parent_0_last_name": "Bianchi",
            "parent_0_relation": "padre",
            "parent_0_birth_date": "1965-02-01",
            "parent_0_occupation": "ingegnere",
            "parent_0_notes": "Presente ma poco espansivo.",
            "parent_1_first_name": "Elena",
            "parent_1_last_name": "Ferrari",
            "parent_1_relation": "madre",
            "parent_1_birth_date": "1968-05-20",
            "parent_1_occupation": "insegnante",
            "sibling_0_first_name": "Luca",
            "sibling_0_last_name": "Bianchi",
            "sibling_0_relation": "fratello",
            "sibling_0_birth_date": "2001-06-01",
            "sibling_0_occupation": "studente",
            "sibling_0_notes": "Rapporto affettuoso ma poco condiviso.",
            "family_notes": "Nucleo unito, comunicazione indiretta.",
            "person_0_display_name": "Marco",
            "person_0_relation": "amico",
            "person_0_trust": "74",
            "person_0_intimacy": "61",
            "person_0_notes": "Amico storico.",
            "education_0_institution": "Università di Milano",
            "education_0_qualification": "Laurea in Lettere",
            "education_0_start_year": "2016",
            "education_0_end_year": "2019",
            "education_0_location": "Milano",
            "work_0_role": "Editor",
            "work_0_organization": "Casa editrice",
            "work_0_start_year": "2020",
            "work_0_location": "Torino",
            "biography_summary": "Cresciuta a Milano, si è trasferita a Torino per lavoro.",
            "current_situation": "Vive a Torino e lavora come editor.",
        }
        values.update(overrides)
        return values

    def _submit(
        self,
        csrf: str,
        form: dict[str, str] | None = None,
        *,
        cookie: str | None = None,
        origin: str | None = None,
    ):
        values = {"csrf": csrf}
        values.update(form or self._form())
        data = urllib.parse.urlencode(values).encode()
        return self._request(
            "/setup",
            data=data,
            cookie=cookie,
            origin=origin or self.base_url,
        )

    def _job_status(self, *, cookie: str | None = None) -> dict:
        response = self._request("/setup/finalize/status", cookie=cookie)
        self.assertEqual(response.status, 200)
        return json.loads(response.read())

    def _wait_for_job(
        self, *, cookie: str | None = None, timeout: float = 15.0
    ) -> dict:
        deadline = time.monotonic() + timeout
        last: dict | None = None
        while time.monotonic() < deadline:
            last = self._job_status(cookie=cookie)
            job = last.get("job")
            if job is not None and job.get("state") == "done":
                return last
            time.sleep(0.02)
        self.fail(f"Job di completamento non terminato: {last}")


class DashboardSetupTests(SetupFixture):
    def test_profile_state_and_empty_root_redirect(self) -> None:
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)
        data = DashboardData(self.root)
        self.assertEqual(data.profile_status(), PROFILE_STATE_MISSING)

        response = self._request("/")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/setup")

        response = self._request("/api/overview")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read())
        self.assertEqual(payload["profile"], PROFILE_STATE_MISSING)

    def test_setup_page_and_rich_profile_creation(self) -> None:
        page, csrf = self._csrf("/setup")
        self.assertIn("Crea il primo profilo", page)
        self.assertIn('name="first_name"', page)
        self.assertIn('data-step="1"', page)
        self.assertIn('data-step="5"', page)
        self.assertIn('data-step="6"', page)
        self.assertIn('data-add-row="parent"', page)
        self.assertIn('name="trait_introversion_extraversion"', page)
        self.assertIn('name="generate_biography"', page)
        self.assertIn('name="build_index"', page)
        self.assertIn('/static/setup.js', page)
        self.assertIn(str(self.root), page)

        response = self._submit(csrf)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/setup/finalize")

        response = self._request("/setup/finalize")
        self.assertEqual(response.status, 200)
        body = response.read().decode("utf-8")
        self.assertIn("La memoria può iniziare", body)
        self.assertIn("Biografia", body)
        self.assertIn("Indice vettoriale", body)
        self.assertIn("setup_complete.js", body)
        self.assertIn("data-status-url=\"/setup/finalize/status\"", body)
        self.assertNotIn("{{", body)
        self.assertIsNone(self._job_status()["job"])

        self.assertEqual(profile_state(self.root), PROFILE_STATE_READY)
        identity = json.loads(
            (self.root / "config" / "identity.json").read_text(encoding="utf-8")
        )
        self.assertEqual(identity["personal_data"]["full_name"], "Giulia Bianchi")
        self.assertEqual(
            identity["personal_data"]["residence"]["city"], "Torino"
        )
        self.assertEqual(
            identity["personal_data"]["residence"]["since"], "2018-09-01"
        )
        self.assertEqual(
            [item["code"] for item in identity["personal_data"]["languages"]],
            ["it", "en"],
        )
        self.assertEqual(
            identity["family"]["parents"][1]["last_name"], "Ferrari"
        )
        self.assertEqual(
            identity["family"]["siblings"][0]["relation"], "fratello"
        )
        self.assertEqual(
            identity["personality"]["traits"]["empathy_detachment"]["value"],
            7,
        )
        self.assertEqual(
            identity["personality"]["traits"]["introversion_extraversion"][
                "description"
            ],
            "Preferisce pochi legami profondi.",
        )
        self.assertEqual(identity["interests"], ["fotografia", "cinema"])
        self.assertEqual(identity["values"], ["onestà", "lealtà"])
        self.assertEqual(identity["biography"]["education"][0]["institution"], "Università di Milano")
        self.assertEqual(identity["biography"]["work"][0]["role"], "Editor")

        social = json.loads(
            (self.root / "config" / "social_graph.json").read_text(encoding="utf-8")
        )
        self.assertEqual(social["people"][0]["display_name"], "Marco")
        self.assertEqual(social["people"][0]["trust"], 74)
        self.assertEqual(social["people"][0]["intimacy"], 61)

        data = DashboardData(self.root)
        overview = data.overview()
        self.assertEqual(overview["counts"]["memories"], 0)
        self.assertEqual(overview["counts"]["semantic_items"], 0)
        self.assertEqual(overview["identity"]["full_name"], "Giulia Bianchi")

        response = self._request("/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"Mnemosine", response.read())

        response = self._request("/setup")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/")

    def test_setup_uses_selects_for_ambiguous_fields(self) -> None:
        page, _csrf = self._csrf("/setup")
        for name in (
            "gender",
            "pronouns",
            "timezone",
            "birth_country",
            "birth_region",
            "birth_province",
            "citizenship",
            "residence_country",
            "residence_region",
            "residence_province",
        ):
            self.assertIn(f'<select name="{name}"', page)
        for name in (
            "birth_region",
            "birth_province",
            "residence_region",
            "residence_province",
        ):
            self.assertNotIn(f'<input name="{name}"', page)
        self.assertNotIn('name="birth_country_code"', page)
        self.assertIn(
            '<option value="Italia" data-code="IT" selected>Italia</option>',
            page,
        )
        self.assertIn('<option value="Francia" data-code="FR"', page)
        self.assertIn(
            '<option value="Europe/Rome" selected>Europe/Rome</option>', page
        )
        self.assertIn(
            '<option value="" selected>Uguale al paese di nascita</option>',
            page,
        )
        self.assertIn('<select name="language___INDEX___code">', page)
        self.assertIn('<select name="language___INDEX___level">', page)
        self.assertIn('data-italian-geo="birth"', page)
        self.assertIn('data-foreign-geo="birth"', page)
        self.assertIn('name="birth_region_foreign"', page)
        self.assertIn('name="birth_province_foreign"', page)
        self.assertIn('list="birth-region-options"', page)
        self.assertIn('list="birth-province-options"', page)
        self.assertIn('id="birth-region-options"', page)
        self.assertIn('id="birth-province-options"', page)
        self.assertIn('data-italian-geo="residence"', page)
        self.assertIn('data-foreign-geo="residence"', page)
        self.assertIn('name="residence_region_foreign"', page)
        self.assertIn('name="residence_province_foreign"', page)
        self.assertIn('list="residence-region-options"', page)
        self.assertIn('list="residence-province-options"', page)
        self.assertIn('id="residence-region-options"', page)
        self.assertIn('id="residence-province-options"', page)
        self.assertIn("Italiano (it)", page)
        self.assertIn('<optgroup label="Lombardia">', page)
        self.assertIn("Milano (MI)", page)
        self.assertIn('data-region="Lombardia"', page)
        self.assertIn('<option value="" selected>Non indicata</option>', page)
        self.assertNotIn("{{", page)

    def test_foreign_geo_catalog_is_served(self) -> None:
        response = self._request("/static/foreign-geo.json")
        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.headers["Content-Type"],
            "application/json; charset=utf-8",
        )
        catalog = json.loads(response.read().decode("utf-8"))
        self.assertIn("Île-de-France", catalog["FR"]["regions"])
        self.assertIn("Paris", catalog["FR"]["provinces"])
        self.assertIn("Bavaria", catalog["DE"]["regions"])
        self.assertTrue(catalog["US"]["regions"])
        self.assertTrue(catalog["US"]["provinces"])

    def test_country_code_and_language_name_are_derived(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_country="Francia",
                birth_country_code="",
                birth_region="",
                birth_province="",
                language_0_name="",
            ),
        )
        self.assertEqual(response.status, 303)
        identity = json.loads(
            (self.root / "config" / "identity.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            identity["personal_data"]["birth_place"]["country"], "Francia"
        )
        self.assertEqual(
            identity["personal_data"]["birth_place"]["country_code"], "FR"
        )
        self.assertEqual(
            identity["personal_data"]["languages"][0],
            {"code": "it", "name": "italiano", "level": "madrelingua"},
        )

    def test_province_and_region_are_derived(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_region="",
                residence_region="",
            ),
        )
        self.assertEqual(response.status, 303)
        identity = json.loads(
            (self.root / "config" / "identity.json").read_text(encoding="utf-8")
        )
        birth_place = identity["personal_data"]["birth_place"]
        self.assertEqual(birth_place["province"], "MI")
        self.assertEqual(birth_place["region"], "Lombardia")
        residence = identity["personal_data"]["residence"]
        self.assertEqual(residence["province"], "TO")
        self.assertEqual(residence["region"], "Piemonte")

    def test_foreign_birth_accepts_local_geo_fields(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_city="Parigi",
                birth_country="Francia",
                birth_region="",
                birth_province="",
                birth_region_foreign="Île-de-France",
                birth_province_foreign="Paris",
            ),
        )
        self.assertEqual(response.status, 303)
        identity = json.loads(
            (self.root / "config" / "identity.json").read_text(encoding="utf-8")
        )
        birth_place = identity["personal_data"]["birth_place"]
        self.assertEqual(birth_place["country"], "Francia")
        self.assertEqual(birth_place["region"], "Île-de-France")
        self.assertEqual(birth_place["province"], "Paris")

    def test_foreign_birth_rejects_italian_geo_values(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_country="Francia",
                birth_region="",
                birth_province="",
                birth_province_foreign="MI",
            ),
        )
        self.assertEqual(response.status, 400)
        self.assertIn(
            "non una sigla italiana",
            response.read().decode("utf-8"),
        )
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_country="Francia",
                birth_region="",
                birth_province="",
                birth_region_foreign="Lazio",
            ),
        )
        self.assertEqual(response.status, 400)
        self.assertIn(
            "non una regione italiana",
            response.read().decode("utf-8"),
        )

    def test_foreign_birth_values_survive_form_errors(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_date="not-a-date",
                birth_country="Francia",
                birth_region="",
                birth_province="",
                birth_region_foreign="Île-de-France",
                birth_province_foreign="Paris",
            ),
        )
        self.assertEqual(response.status, 400)
        page = response.read().decode("utf-8")
        self.assertIn('value="Île-de-France"', page)
        self.assertIn('value="Paris"', page)

    def test_residence_inherits_foreign_birth_geo(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_city="Parigi",
                birth_country="Francia",
                birth_region="",
                birth_province="",
                birth_region_foreign="Île-de-France",
                birth_province_foreign="Paris",
                residence_city="",
                residence_country="",
                residence_region="",
                residence_province="",
            ),
        )
        self.assertEqual(response.status, 303)
        identity = json.loads(
            (self.root / "config" / "identity.json").read_text(encoding="utf-8")
        )
        residence = identity["personal_data"]["residence"]
        self.assertEqual(residence["country"], "Francia")
        self.assertEqual(residence["region"], "Île-de-France")
        self.assertEqual(residence["province"], "Paris")

    def test_invalid_province_or_region_is_rejected(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(csrf, self._form(birth_province="XYZ"))
        self.assertEqual(response.status, 400)
        self.assertIn(
            "scegli una provincia",
            response.read().decode("utf-8"),
        )
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(birth_region="Lazio", birth_province="MI"),
        )
        self.assertEqual(response.status, 400)
        self.assertIn(
            "non appartiene alla regione Lazio",
            response.read().decode("utf-8"),
        )
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(birth_country="Francia", birth_region="", birth_province="MI"),
        )
        self.assertEqual(response.status, 400)
        self.assertIn(
            "non una sigla italiana",
            response.read().decode("utf-8"),
        )
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

    def test_residence_does_not_inherit_foreign_province(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                residence_city="Parigi",
                residence_country="Francia",
                residence_province="",
                residence_region="",
            ),
        )
        self.assertEqual(response.status, 303)
        identity = json.loads(
            (self.root / "config" / "identity.json").read_text(encoding="utf-8")
        )
        residence = identity["personal_data"]["residence"]
        self.assertEqual(residence["country"], "Francia")
        self.assertEqual(residence["province"], "")
        self.assertEqual(residence["region"], "")

    def test_foreign_residence_accepts_local_geo_fields(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                residence_city="Parigi",
                residence_country="Francia",
                residence_region_foreign="Île-de-France",
                residence_province_foreign="Paris",
            ),
        )
        self.assertEqual(response.status, 303)
        identity = json.loads(
            (self.root / "config" / "identity.json").read_text(encoding="utf-8")
        )
        residence = identity["personal_data"]["residence"]
        self.assertEqual(residence["country"], "Francia")
        self.assertEqual(residence["region"], "Île-de-France")
        self.assertEqual(residence["province"], "Paris")

    def test_foreign_residence_uses_birth_country_when_blank(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_city="Parigi",
                birth_country="Francia",
                birth_province="",
                birth_region="",
                residence_city="Parigi",
                residence_country="",
                residence_region_foreign="Île-de-France",
                residence_province_foreign="Paris",
            ),
        )
        self.assertEqual(response.status, 303)
        identity = json.loads(
            (self.root / "config" / "identity.json").read_text(encoding="utf-8")
        )
        residence = identity["personal_data"]["residence"]
        self.assertEqual(residence["country"], "Francia")
        self.assertEqual(residence["region"], "Île-de-France")
        self.assertEqual(residence["province"], "Paris")

    def test_foreign_residence_rejects_italian_geo_values(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                residence_country="Francia",
                residence_province_foreign="MI",
            ),
        )
        self.assertEqual(response.status, 400)
        self.assertIn(
            "non una sigla italiana",
            response.read().decode("utf-8"),
        )

        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                residence_country="Francia",
                residence_region_foreign="Lazio",
            ),
        )
        self.assertEqual(response.status, 400)
        self.assertIn(
            "non una regione italiana",
            response.read().decode("utf-8"),
        )

    def test_foreign_residence_values_survive_form_errors(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(
                birth_date="not-a-date",
                residence_country="Francia",
                residence_region_foreign="Île-de-France",
                residence_province_foreign="Paris",
            ),
        )
        self.assertEqual(response.status, 400)
        page = response.read().decode("utf-8")
        self.assertIn('value="Île-de-France"', page)
        self.assertIn('value="Paris"', page)

    def test_unknown_country_is_rejected(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(csrf, self._form(birth_country="Atlantide"))
        self.assertEqual(response.status, 400)
        self.assertIn(
            "scegli un paese",
            response.read().decode("utf-8"),
        )
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

    def test_invalid_form_does_not_create_profile(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(birth_date="1998-02-31"),
        )
        self.assertEqual(response.status, 400)
        body = response.read().decode("utf-8")
        self.assertIn("Data non valida", body)
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        _page, csrf = self._csrf("/setup")
        response = self._submit(csrf, self._form(first_name=""))
        self.assertEqual(response.status, 400)
        self.assertIn("nome", response.read().decode("utf-8").lower())
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

    def test_invalid_relationships_and_timeline_are_rejected(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(parent_0_birth_date="2000-01-01"),
        )
        self.assertEqual(response.status, 400)
        self.assertIn("precedere", response.read().decode("utf-8"))
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(education_0_end_year="2010"),
        )
        self.assertEqual(response.status, 400)
        self.assertIn("anno di fine", response.read().decode("utf-8").lower())
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(education_0_start_year="1990"),
        )
        self.assertEqual(response.status, 400)
        self.assertIn("precedere la nascita", response.read().decode("utf-8"))
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(person_0_trust="101"),
        )
        self.assertEqual(response.status, 400)
        self.assertIn("valore massimo 100", response.read().decode("utf-8"))
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

    def test_error_page_preserves_values_and_escapes_them(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            self._form(first_name="<b>Giulia</b>", timezone="Non/Valida"),
        )
        self.assertEqual(response.status, 400)
        body = response.read().decode("utf-8")
        self.assertIn("Timezone IANA non valida", body)
        self.assertIn("&lt;b&gt;Giulia&lt;/b&gt;", body)
        self.assertNotIn("<b>Giulia</b>", body)
        self.assertIn("data-values", body)
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

    def test_invalid_csrf_and_cross_origin_are_rejected(self) -> None:
        response = self._submit("token-inventato")
        self.assertEqual(response.status, 400)
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        _page, csrf = self._csrf("/setup")
        response = self._submit(
            csrf,
            origin="https://evil.example",
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

    def test_incomplete_profile_is_not_overwritten(self) -> None:
        _page, csrf = self._csrf("/setup")
        self.assertEqual(self._submit(csrf).status, 303)

        (self.root / "data" / "vector_index.db").unlink()
        self.assertEqual(profile_state(self.root), PROFILE_STATE_INCOMPLETE)

        response = self._request("/setup")
        self.assertEqual(response.status, 409)
        body = response.read().decode("utf-8")
        self.assertIn("profilo incompleto", body)
        self.assertEqual(profile_state(self.root), PROFILE_STATE_INCOMPLETE)

    def test_biography_pipeline_and_retry_page(self) -> None:
        _page, csrf = self._csrf("/setup")
        response = self._submit(csrf, self._form(generate_biography="1"))
        self.assertEqual(response.status, 303)
        payload = self._wait_for_job()
        job = payload["job"]
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["steps"][0]["state"], "success")
        self.assertIn("ricordi creati", job["steps"][0]["text"])

        overview = DashboardData(self.root).overview()
        self.assertGreater(overview["counts"]["memories"], 0)

        page = self._request("/setup/finalize").read().decode("utf-8")
        self.assertIn("ricordi creati", page)
        self.assertIn("Rigenera biografia", page)

        page, retry_csrf = self._csrf("/setup/finalize")
        self.assertIn("Ricarica stato", page)
        self.assertIn('value="biography"', page)
        data = urllib.parse.urlencode(
            {"csrf": retry_csrf, "action": "biography"}
        ).encode()
        response = self._request(
            "/setup/finalize", data=data, origin=self.base_url
        )
        self.assertEqual(response.status, 303)
        self._wait_for_job()
        page = self._request("/setup/finalize").read().decode("utf-8")
        self.assertIn("0 ricordi creati", page)

    def test_index_pipeline_uses_completion_page(self) -> None:
        fake = type(
            "FakeRetrieval",
            (),
            {
                "__init__": lambda self, root: None,
                "sync_index": lambda self, rebuild=False: {
                    "ok": True,
                    "indexes": {
                        "episodic": {"count": 3, "status": "ready"},
                        "semantic": {"count": 1, "status": "ready"},
                    },
                },
            },
        )
        _page, csrf = self._csrf("/setup")
        with patch("agent_memory.dashboard.RetrievalService", fake):
            response = self._submit(csrf, self._form(build_index="1"))
            self.assertEqual(response.status, 303)
            payload = self._wait_for_job()
            self.assertEqual(payload["job"]["steps"][0]["state"], "success")
            body = self._request("/setup/finalize").read().decode("utf-8")
        self.assertIn("Indice pronto", body)
        self.assertIn("ricordi: 3", body)

    def test_index_failure_keeps_profile_and_offers_retry(self) -> None:
        _page, csrf = self._csrf("/setup")
        self.assertEqual(self._submit(csrf, self._form()).status, 303)

        page, retry_csrf = self._csrf("/setup/finalize")
        self.assertIn("Nessun ricordo biografico", page)
        self.assertIn("Costruisci indice", page)

        fake = type(
            "FakeRetrieval",
            (),
            {
                "__init__": lambda self, root: None,
                "sync_index": lambda self, rebuild=False: {
                    "ok": False,
                    "indexes": {
                        "episodic": {"error": "modello di embedding assente"}
                    },
                },
            },
        )
        data = urllib.parse.urlencode(
            {"csrf": retry_csrf, "action": "index"}
        ).encode()
        with patch("agent_memory.dashboard.RetrievalService", fake):
            response = self._request(
                "/setup/finalize", data=data, origin=self.base_url
            )
            self.assertEqual(response.status, 303)
            payload = self._wait_for_job()
            self.assertEqual(payload["job"]["steps"][0]["state"], "error")
            body = self._request("/setup/finalize").read().decode("utf-8")
        self.assertIn("modello di embedding assente", body)
        self.assertIn("Costruisci indice", body)
        self.assertEqual(profile_state(self.root), PROFILE_STATE_READY)

    def test_finalize_runs_in_background_with_progress(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingBiography:
            def __init__(self, root: Path) -> None:
                self.root = root

            def apply(self) -> dict:
                started.set()
                release.wait(5)
                return {"created": 1, "skipped": 0, "total": 1}

        _page, csrf = self._csrf("/setup")
        with patch("agent_memory.dashboard.BiographyService", BlockingBiography):
            try:
                response = self._submit(
                    csrf, self._form(generate_biography="1")
                )
                self.assertEqual(response.status, 303)
                self.assertTrue(started.wait(2))
                payload = self._job_status()
                job = payload["job"]
                self.assertEqual(job["state"], "running")
                self.assertEqual(job["steps"][0]["state"], "running")
                self.assertGreater(job["progress"], 0)
                self.assertLess(job["progress"], 100)

                page = self._request("/setup/finalize").read().decode("utf-8")
                self.assertIn('data-running="1"', page)
                self.assertIn('id="finalize-biography-button" disabled', page)
                self.assertIn("setup_complete.js", page)
                self.assertNotIn("{{", page)
            finally:
                release.set()
            payload = self._wait_for_job()
        self.assertEqual(payload["job"]["state"], "done")
        self.assertEqual(payload["job"]["steps"][0]["state"], "success")
        self.assertEqual(payload["job"]["progress"], 100)

    def test_setup_template_is_not_public(self) -> None:
        response = self._request("/static/setup.html")
        self.assertEqual(response.status, 404)

        response = self._request("/static/setup_complete.html")
        self.assertEqual(response.status, 404)

        response = self._request("/static/setup.js")
        self.assertEqual(response.status, 200)
        self.assertIn(b"setup-form", response.read())

        response = self._request("/static/setup_complete.js")
        self.assertEqual(response.status, 200)
        self.assertIn(b"finalize-job", response.read())


class DashboardSetupAuthTests(SetupFixture):
    auth = DashboardAuth(PASSWORD)

    def _login(self) -> str:
        page, csrf = self._csrf("/login")
        self.assertIn("Accedi alla dashboard", page)
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "/setup"}
        ).encode()
        response = self._request("/login", data=data, origin=self.base_url)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/setup")
        return response.headers["Set-Cookie"].split(";", 1)[0]

    def test_setup_requires_login_and_creates_profile(self) -> None:
        response = self._request("/setup")
        self.assertEqual(response.status, 303)
        self.assertEqual(
            response.headers["Location"], "/login?next=%2Fsetup"
        )

        cookie = self._login()
        response = self._request("/setup", cookie=cookie)
        self.assertEqual(response.status, 200)
        page = response.read().decode("utf-8")
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        self.assertIsNotNone(match)
        csrf = match.group(1)

        response = self._submit(csrf, cookie=cookie)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/setup/finalize")
        self.assertEqual(profile_state(self.root), PROFILE_STATE_READY)

        response = self._request("/setup/finalize", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn(b"setup_complete.js", response.read())

        response = self._request("/api/overview", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["counts"]["memories"], 0)


if __name__ == "__main__":
    unittest.main()
