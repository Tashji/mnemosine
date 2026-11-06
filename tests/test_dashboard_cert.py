"""Test dei comandi di esportazione e trust store del certificato."""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory.dashboard_cert import (
    TrustStorePlan,
    UninstallPlan,
    build_trust_plan,
    build_uninstall_plan,
    certificate_fingerprint,
    export_certificate,
    install_certificate,
    is_elevated,
    main,
    uninstall_certificate,
)
from agent_memory.dashboard_tls import ensure_dashboard_tls
from agent_memory.errors import ConfigError


class DashboardCertificateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tls_dir = self.root / "tls"
        self.tls = ensure_dashboard_tls(self.tls_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_export_and_fingerprint(self) -> None:
        exported = self.root / "export" / "mnemosine-dashboard.crt"
        destination = export_certificate(
            self.tls.certificate_path, exported
        )
        self.assertEqual(destination, exported.resolve())
        self.assertEqual(
            destination.read_bytes(),
            self.tls.certificate_path.read_bytes(),
        )
        fingerprint = certificate_fingerprint(destination)
        self.assertRegex(
            fingerprint,
            re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$"),
        )

        with self.assertRaises(ConfigError):
            export_certificate(self.tls.certificate_path, exported)
        export_certificate(self.tls.certificate_path, exported, force=True)

    def test_trust_store_plans(self) -> None:
        certificate = self.tls.certificate_path
        linux = build_trust_plan(
            certificate,
            system_name="Linux",
            which=lambda name: (
                "/usr/sbin/update-ca-certificates"
                if name == "update-ca-certificates"
                else None
            ),
        )
        self.assertEqual(linux.system, "linux")
        self.assertIsNotNone(linux.copy_to)
        self.assertIn("update-ca-certificates", linux.commands[0][0])

        redhat = build_trust_plan(
            certificate,
            system_name="Linux",
            which=lambda name: (
                "/usr/bin/update-ca-trust" if name == "update-ca-trust" else None
            ),
        )
        self.assertIn("update-ca-trust", redhat.commands[0][0])
        self.assertIn("ca-trust", str(redhat.copy_to))

        arch = build_trust_plan(
            certificate,
            system_name="Linux",
            which=lambda name: "/usr/bin/trust" if name == "trust" else None,
        )
        self.assertEqual(arch.commands[0][1], "extract-compat")

        macos = build_trust_plan(
            certificate,
            system_name="Darwin",
            which=lambda name: "/usr/bin/security" if name == "security" else None,
        )
        self.assertEqual(macos.system, "darwin")
        self.assertIsNone(macos.copy_to)
        self.assertIn("add-trusted-cert", macos.commands[0])
        self.assertIn(str(certificate.resolve()), macos.commands[0])

        windows = build_trust_plan(
            certificate,
            system_name="Windows",
            which=lambda name: "C:\\Windows\\System32\\certutil.exe"
            if name == "certutil"
            else None,
        )
        self.assertEqual(windows.system, "windows")
        self.assertIsNone(windows.copy_to)
        self.assertIn("-addstore", windows.commands[0])

        with self.assertRaises(ConfigError):
            build_trust_plan(
                certificate,
                system_name="Plan9",
                which=lambda _name: None,
            )

    def test_uninstall_trust_store_plans(self) -> None:
        certificate = self.tls.certificate_path
        linux = build_uninstall_plan(
            certificate,
            system_name="Linux",
            which=lambda name: (
                "/usr/sbin/update-ca-certificates"
                if name == "update-ca-certificates"
                else None
            ),
        )
        self.assertEqual(linux.system, "linux")
        self.assertEqual(
            linux.remove_from,
            Path("/usr/local/share/ca-certificates/mnemosine-dashboard.crt"),
        )
        self.assertEqual(linux.commands[0][1], "--fresh")

        redhat = build_uninstall_plan(
            certificate,
            system_name="Linux",
            which=lambda name: (
                "/usr/bin/update-ca-trust" if name == "update-ca-trust" else None
            ),
        )
        self.assertIn("ca-trust", str(redhat.remove_from))
        self.assertEqual(redhat.commands[0], (redhat.commands[0][0],))

        arch = build_uninstall_plan(
            certificate,
            system_name="Linux",
            which=lambda name: "/usr/bin/trust" if name == "trust" else None,
        )
        self.assertEqual(arch.commands[0][1], "extract-compat")

        macos = build_uninstall_plan(
            certificate,
            system_name="Darwin",
            which=lambda name: "/usr/bin/security" if name == "security" else None,
        )
        self.assertEqual(macos.system, "darwin")
        self.assertIsNone(macos.remove_from)
        self.assertIn("delete-certificate", macos.commands[0])
        self.assertIn("-Z", macos.commands[0])
        self.assertRegex(macos.commands[0][3], re.compile(r"^[0-9A-F]{40}$"))

        windows = build_uninstall_plan(
            certificate,
            system_name="Windows",
            which=lambda name: "C:\\Windows\\System32\\certutil.exe"
            if name == "certutil"
            else None,
        )
        self.assertEqual(windows.system, "windows")
        self.assertIn("-delstore", windows.commands[0])
        self.assertRegex(windows.commands[0][3], re.compile(r"^[0-9A-F]{40}$"))

        with self.assertRaises(ConfigError):
            build_uninstall_plan(
                certificate,
                system_name="Plan9",
                which=lambda _name: None,
            )

    def test_uninstall_removes_file_and_runs_commands(self) -> None:
        destination = self.root / "trust" / "mnemosine-dashboard.crt"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(self.tls.certificate_path.read_bytes())
        plan = UninstallPlan(
            system="test",
            remove_from=destination,
            commands=((sys.executable, "-c", "raise SystemExit(0)"),),
            note="Piano di prova.",
        )
        with patch(
            "agent_memory.dashboard_cert.is_elevated", return_value=True
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = uninstall_certificate(self.tls.certificate_path, plan)
        self.assertEqual(code, 0)
        self.assertFalse(destination.exists())
        self.assertIn("Certificato rimosso", stdout.getvalue())

    def test_cli_export_and_fingerprint(self) -> None:
        output = self.root / "exported.crt"
        code, stdout, stderr = self._run(
            [
                "--tls-dir",
                str(self.tls_dir),
                "export",
                "--output",
                str(output),
            ]
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(output.is_file())
        self.assertIn("Certificato esportato:", stdout)
        self.assertIn("SHA-256:", stdout)

        code, stdout, stderr = self._run(
            ["--tls-dir", str(self.tls_dir), "fingerprint"]
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("SHA-256:", stdout)

    def test_install_copies_and_runs_commands(self) -> None:
        destination = self.root / "trust" / "mnemosine-dashboard.crt"
        plan = TrustStorePlan(
            system="test",
            copy_to=destination,
            commands=((sys.executable, "-c", "raise SystemExit(0)"),),
            note="Piano di prova.",
        )
        with patch(
            "agent_memory.dashboard_cert.is_elevated", return_value=True
        ), contextlib.redirect_stdout(io.StringIO()):
            code = install_certificate(self.tls.certificate_path, plan)
        self.assertEqual(code, 0)
        self.assertEqual(
            destination.read_bytes(),
            self.tls.certificate_path.read_bytes(),
        )

    def test_install_is_dry_run_without_yes(self) -> None:
        try:
            build_trust_plan(self.tls.certificate_path)
        except ConfigError as exc:
            self.skipTest(f"Trust store non disponibile: {exc}")

        code, stdout, stderr = self._run(
            ["--tls-dir", str(self.tls_dir), "install", "--dry-run"]
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry-run: nessuna modifica eseguita.", stdout)

        code, stdout, stderr = self._run(
            ["--tls-dir", str(self.tls_dir), "install"]
        )
        self.assertEqual(code, 2)
        self.assertIn("Nessuna modifica eseguita", stdout)
        self.assertIn("--yes", stdout)

    def test_cli_uninstall_is_dry_run_without_yes(self) -> None:
        try:
            build_uninstall_plan(self.tls.certificate_path)
        except ConfigError as exc:
            self.skipTest(f"Trust store non disponibile: {exc}")

        code, stdout, stderr = self._run(
            ["--tls-dir", str(self.tls_dir), "uninstall", "--dry-run"]
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry-run: nessuna modifica eseguita.", stdout)

        code, stdout, stderr = self._run(
            ["--tls-dir", str(self.tls_dir), "uninstall"]
        )
        self.assertEqual(code, 2)
        self.assertIn("Nessuna modifica eseguita", stdout)
        self.assertIn("--yes", stdout)

    def test_cli_uninstall_does_not_generate_a_certificate(self) -> None:
        missing = self.root / "missing"
        code, _stdout, stderr = self._run(
            ["--tls-dir", str(missing), "uninstall", "--dry-run"]
        )
        self.assertEqual(code, 2)
        self.assertIn("Certificato non trovato", stderr)
        self.assertFalse((missing / "cert.pem").exists())

    @unittest.skipIf(is_elevated(), "test eseguito come amministratore")
    def test_install_requires_privileges(self) -> None:
        try:
            build_trust_plan(self.tls.certificate_path)
        except ConfigError as exc:
            self.skipTest(f"Trust store non disponibile: {exc}")
        code, _stdout, stderr = self._run(
            ["--tls-dir", str(self.tls_dir), "install", "--yes"]
        )
        self.assertEqual(code, 2)
        self.assertIn("privilegi amministrativi", stderr)

    @unittest.skipIf(is_elevated(), "test eseguito come amministratore")
    def test_uninstall_requires_privileges(self) -> None:
        try:
            build_uninstall_plan(self.tls.certificate_path)
        except ConfigError as exc:
            self.skipTest(f"Trust store non disponibile: {exc}")
        code, _stdout, stderr = self._run(
            ["--tls-dir", str(self.tls_dir), "uninstall", "--yes"]
        )
        self.assertEqual(code, 2)
        self.assertIn("privilegi amministrativi", stderr)


if __name__ == "__main__":
    unittest.main()
