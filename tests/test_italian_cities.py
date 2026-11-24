"""Test della normalizzazione dei comuni italiani."""

from __future__ import annotations

import json
import unittest

from agent_memory.italian_cities import (
    CITY_DATA_PATH,
    clean_city_name,
    normalize_italian_city,
)


class ItalianCitiesTest(unittest.TestCase):
    def test_catalog_has_italian_municipalities_and_attribution(self) -> None:
        payload = json.loads(CITY_DATA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["_license"], "CC BY 4.0")
        self.assertIn("RP92/comuni-italiani", payload["_source"])
        self.assertIn("Ragusa", payload["provinces"]["RG"])
        self.assertGreaterEqual(
            sum(len(names) for names in payload["provinces"].values()), 7000
        )

    def test_exact_city_is_returned_with_province(self) -> None:
        match = normalize_italian_city("ragusa", "RG")
        self.assertTrue(match.matched)
        self.assertTrue(match.exact)
        self.assertEqual(match.name, "Ragusa")
        self.assertEqual(match.province, "RG")

    def test_typo_is_corrected_inside_the_selected_province(self) -> None:
        match = normalize_italian_city("Raguss", "RG")
        self.assertTrue(match.matched)
        self.assertFalse(match.exact)
        self.assertEqual(match.name, "Ragusa")
        self.assertEqual(match.province, "RG")

    def test_typo_without_province_infers_unique_province(self) -> None:
        match = normalize_italian_city("Torinno")
        self.assertTrue(match.matched)
        self.assertEqual(match.name, "Torino")
        self.assertEqual(match.province, "TO")

    def test_accents_apostrophes_and_articles_are_normalized(self) -> None:
        self.assertEqual(normalize_italian_city("aglIE", "TO").name, "Agliè")
        self.assertEqual(normalize_italian_city("Aquila", "AQ").name, "L'Aquila")
        self.assertEqual(
            normalize_italian_city("  la  spezia ", "SP").name, "La Spezia"
        )

    def test_unknown_city_is_preserved_in_readable_form(self) -> None:
        match = normalize_italian_city("VATTELAPESCA", "RG")
        self.assertFalse(match.matched)
        self.assertEqual(match.name, "Vattelapesca")
        self.assertEqual(match.province, "RG")

    def test_clean_city_name_removes_typographic_apostrophes_and_spaces(self) -> None:
        self.assertEqual(
            clean_city_name("  Sant’  Angelo  "),
            "Sant' Angelo",
        )


if __name__ == "__main__":
    unittest.main()
