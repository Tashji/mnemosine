"""Test della sandbox dei file locali dell'agente."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_memory.errors import ConflictError, NotFoundError, ValidationError
from agent_memory.files import FileSandbox


class FileSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sandbox = FileSandbox(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_then_read_roundtrip(self) -> None:
        created = self.sandbox.write_file("note/promemoria.txt", "contenuto\n")
        self.assertEqual(created["path"], "note/promemoria.txt")
        self.assertTrue(created["created"])
        self.assertFalse(created["overwritten"])
        self.assertEqual(created["size"], len("contenuto\n"))

        read = self.sandbox.read_file("note/promemoria.txt")
        self.assertEqual(read["text"], "contenuto\n")
        self.assertEqual(read["size"], len("contenuto\n"))
        self.assertFalse(read["truncated"])

    def test_sandbox_is_inside_profile(self) -> None:
        self.sandbox.write_file("a.txt", "x")
        self.assertEqual(
            (self.root / "files" / "a.txt").read_text(encoding="utf-8"), "x"
        )

    def test_read_truncates_at_max_chars(self) -> None:
        self.sandbox.write_file("lungo.txt", "abcdefghij")
        read = self.sandbox.read_file("lungo.txt", max_chars=4)
        self.assertEqual(read["text"], "abcd")
        self.assertTrue(read["truncated"])

    def test_rejects_absolute_and_parent_paths(self) -> None:
        blocked = [
            "/etc/passwd",
            "../fuori.txt",
            "a/../../fuori.txt",
            "~/segreto.txt",
            "\\\\server\\share",
            "C:\\Windows\\win.ini",
            "a/./b/../../fuori.txt",
        ]
        for path in blocked:
            with self.subTest(path=path):
                with self.assertRaises(ValidationError):
                    self.sandbox.write_file(path, "x")
                with self.assertRaises(ValidationError):
                    self.sandbox.read_file(path)

    def test_rejects_control_characters_and_long_paths(self) -> None:
        with self.assertRaises(ValidationError):
            self.sandbox.write_file("nome\ncon\nnewline.txt", "x")
        with self.assertRaises(ValidationError):
            self.sandbox.write_file("a" * 600, "x")

    def test_rejects_symlink_escape(self) -> None:
        outside = self.root / "segreto.txt"
        outside.write_text("non deve uscire", encoding="utf-8")
        sandbox_dir = self.sandbox.ensure()
        (sandbox_dir / "link.txt").symlink_to(outside)
        (sandbox_dir / "cartella").mkdir()
        (sandbox_dir / "cartella" / "link").symlink_to(self.root)

        with self.assertRaises(ValidationError):
            self.sandbox.read_file("link.txt")
        with self.assertRaises(ValidationError):
            self.sandbox.read_file("cartella/link/segreto.txt")
        with self.assertRaises(ValidationError):
            self.sandbox.write_file("cartella/link/nuovo.txt", "x")

        listing = self.sandbox.list_files()
        kinds = {entry["name"]: entry["type"] for entry in listing["entries"]}
        self.assertEqual(kinds["link.txt"], "symlink")

    def test_existing_file_requires_overwrite(self) -> None:
        self.sandbox.write_file("a.txt", "primo")
        with self.assertRaises(ConflictError):
            self.sandbox.write_file("a.txt", "secondo")
        updated = self.sandbox.write_file("a.txt", "secondo", overwrite=True)
        self.assertTrue(updated["overwritten"])
        self.assertFalse(updated["created"])
        self.assertEqual(self.sandbox.read_file("a.txt")["text"], "secondo")

    def test_write_rejects_directory_target(self) -> None:
        self.sandbox.write_file("cartella/a.txt", "x")
        with self.assertRaises(ValidationError):
            self.sandbox.write_file("cartella", "x")
        with self.assertRaises(ValidationError):
            self.sandbox.write_file("", "x")

    def test_read_limits_and_binary_detection(self) -> None:
        self.sandbox.write_file("binario.dat", "testo")
        binary = self.sandbox.base / "binario.dat"
        binary.write_bytes(b"\x00\x01\x02")
        with self.assertRaises(ValidationError):
            self.sandbox.read_file("binario.dat")

        latin = self.sandbox.base / "latin.txt"
        latin.write_bytes(b"caff\xe8")
        with self.assertRaises(ValidationError):
            self.sandbox.read_file("latin.txt")

        with self.assertRaises(ValidationError):
            self.sandbox.read_file("binario.dat", max_chars=0)

    def test_read_rejects_large_file(self) -> None:
        self.sandbox.write_file("grande.txt", "x")
        target = self.sandbox.base / "grande.txt"
        target.write_bytes(b"a" * (1_048_576 + 1))
        with self.assertRaises(ValidationError):
            self.sandbox.read_file("grande.txt")

    def test_read_missing_file_and_directory(self) -> None:
        with self.assertRaises(NotFoundError):
            self.sandbox.read_file("manca.txt")
        self.sandbox.write_file("cartella/a.txt", "x")
        with self.assertRaises(ValidationError):
            self.sandbox.read_file("cartella")

    def test_list_files_reports_types_and_limits(self) -> None:
        self.sandbox.write_file("b.txt", "12345")
        self.sandbox.write_file("sotto/a.txt", "x")
        listing = self.sandbox.list_files()
        self.assertEqual(listing["count"], 2)
        self.assertFalse(listing["truncated"])
        entries = {entry["name"]: entry for entry in listing["entries"]}
        self.assertEqual(entries["b.txt"]["type"], "file")
        self.assertEqual(entries["b.txt"]["size"], 5)
        self.assertEqual(entries["sotto"]["type"], "directory")
        self.assertEqual(entries["b.txt"]["path"], "b.txt")

        limited = self.sandbox.list_files(limit=1)
        self.assertEqual(limited["count"], 1)
        self.assertTrue(limited["truncated"])
        with self.assertRaises(ValidationError):
            self.sandbox.list_files(limit=0)

    def test_list_missing_folder_is_empty(self) -> None:
        listing = self.sandbox.list_files("mai/creata")
        self.assertEqual(listing["count"], 0)
        self.assertEqual(listing["entries"], [])

    def test_status_hides_absolute_path(self) -> None:
        status = self.sandbox.status()
        self.assertEqual(status["root"], "files")
        self.assertNotIn(str(self.root), str(status))

    def test_write_rejects_oversized_content(self) -> None:
        with self.assertRaises(ValidationError):
            self.sandbox.write_file("grande.txt", "x" * 262_145)
        with self.assertRaises(ValidationError):
            self.sandbox.write_file("a.txt", b"byte")  # type: ignore[arg-type]

    def test_nested_write_creates_directories(self) -> None:
        result = self.sandbox.write_file("uno/due/tre.txt", "ok")
        self.assertEqual(result["path"], "uno/due/tre.txt")
        self.assertTrue((self.sandbox.base / "uno" / "due" / "tre.txt").is_file())


if __name__ == "__main__":
    unittest.main()
