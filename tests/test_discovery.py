"""Guardia sulla discovery: nessun test deve essere raccolto due volte.

La suite si importa fra moduli (``tests.support``, ``tests.test_browser`` e la
classe base dei test operativi). Importare una classe ``TestCase`` dentro un
modulo la faceva raccogliere di nuovo, e senza ``tests/__init__.py`` lo stesso
file veniva caricato sia come ``test_x`` sia come ``tests.test_x``: entrambe le
cose eseguivano gli stessi test più volte.
"""

from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _ids(suite: unittest.TestSuite) -> list[str]:
    collected: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            collected.extend(_ids(item))
        else:
            collected.append(item.id())
    return collected


class DiscoveryTests(unittest.TestCase):
    def test_every_test_is_collected_once(self) -> None:
        suite = unittest.TestLoader().discover(
            str(PROJECT_ROOT / "tests"), top_level_dir=str(PROJECT_ROOT)
        )
        ids = _ids(suite)
        duplicated = sorted({test_id for test_id in ids if ids.count(test_id) > 1})

        self.assertEqual(duplicated, [], f"test raccolti più volte: {duplicated}")
        self.assertTrue(ids)


if __name__ == "__main__":
    unittest.main()
