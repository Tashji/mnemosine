"""Test del catalogo delle città estere usato dal wizard."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

FOREIGN_CITY_PATH = (
    Path(__file__).resolve().parents[1]
    / "agent_memory"
    / "dashboard_assets"
    / "foreign-cities.json"
)


class ForeignCitiesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(FOREIGN_CITY_PATH.read_text(encoding="utf-8"))
        cls.countries = cls.payload["countries"]

    def test_catalog_has_countries_and_attribution(self) -> None:
        self.assertEqual(self.payload["_license"], "CC BY 4.0")
        self.assertIn("GeoNames cities15000", self.payload["_source"])
        self.assertNotIn("IT", self.countries)
        self.assertGreaterEqual(len(self.countries), 200)
        self.assertGreaterEqual(
            sum(len(country["cities"]) for country in self.countries.values()),
            30000,
        )

    def test_city_record_points_to_region_and_province(self) -> None:
        france = self.countries["FR"]
        record = france["cities"]["paris"]
        self.assertEqual(record[0], "Paris")
        self.assertEqual(france["regions"][record[1]], "Île-de-France")
        self.assertEqual(france["provinces"][record[2]], "Paris")

    def test_italian_exonym_maps_to_canonical_city(self) -> None:
        france = self.countries["FR"]
        self.assertEqual(france["aliases"]["parigi"], "paris")
        self.assertIn("Parigi", france["alias_names"])
        self.assertIn("Lione", france["alias_names"])

    def test_records_have_valid_indexes(self) -> None:
        for code, country in self.countries.items():
            self.assertIsInstance(country["regions"], list)
            self.assertIsInstance(country["provinces"], list)
            for name, record in country["cities"].items():
                with self.subTest(country=code, city=name):
                    self.assertEqual(len(record), 3)
                    self.assertIsInstance(record[0], str)
                    self.assertGreaterEqual(record[1], -1)
                    self.assertGreaterEqual(record[2], -1)
                    if record[1] >= 0:
                        self.assertLess(record[1], len(country["regions"]))
                    if record[2] >= 0:
                        self.assertLess(record[2], len(country["provinces"]))


if __name__ == "__main__":
    unittest.main()