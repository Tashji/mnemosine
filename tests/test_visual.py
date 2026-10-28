"""Test del profilo visivo e del provider Replicate."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from agent_memory.config import ConfigStore
from agent_memory.errors import ConfigError, ValidationError
from agent_memory.visual import MAX_GENERATED_BYTES, MAX_REFERENCE_BYTES, VisualService
from tests.support import copy_profile

_buffer = BytesIO()
Image.new("RGB", (2, 2), (40, 80, 120)).save(_buffer, format="PNG")
PNG = _buffer.getvalue()


class FakeFileOutput:
    url = "https://replicate.delivery/test-image"


def png_data():
    return PNG


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        copy_profile(self.root)
        self.service = VisualService(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reference_is_validated_stored_privately_and_removable(self):
        self.assertFalse(self.service.status()["reference_available"])
        with self.assertRaises(ValidationError):
            self.service.save_reference(b"not an image", "image/png")
        with self.assertRaises(ValidationError):
            self.service.save_reference(PNG, "image/jpeg")
        self.assertTrue(self.service.save_reference(PNG, "image/png")["reference_available"])
        reference = self.root / "data" / "visual" / "reference.jpg"
        self.assertTrue(reference.read_bytes().startswith(bytes.fromhex("ffd8")))
        self.assertEqual(reference.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.service.remove_reference()["removed"])
        self.assertFalse(reference.exists())

    def test_reference_limits_and_seed_validation(self):
        with self.assertRaises(ValidationError):
            self.service.save_reference(PNG + b"x" * MAX_REFERENCE_BYTES, "image/png")
        with self.assertRaises(ValidationError):
            self.service.save_profile({"body": "x" * 3001})
        with self.assertRaises(ValidationError):
            self.service.save_profile({"fixed_seed": True})
        with self.assertRaises(ValidationError):
            self.service.save_profile({"fixed_seed": 2**32})

    def test_mcp_generation_is_blocked_without_dashboard_consent(self):
        ConfigStore(self.root).update_current_state(
            {"flags": {"visual_generation_enabled": True}}, allow_visual_generation=True
        )
        with self.assertRaises(ValidationError):
            self.service.generate_image("un paesaggio")

    def test_generation_requires_enabled_flag_consent_reference_and_token(self):
        with self.assertRaises(ValidationError):
            self.service.generate("una scena", consent_to_external_processing=True)
        ConfigStore(self.root).update_current_state(
            {"flags": {"visual_generation_enabled": True}}, allow_visual_generation=True
        )
        with self.assertRaises(ValidationError):
            self.service.generate("una scena", consent_to_external_processing=False)
        with (
            self.assertRaises(ConfigError),
            patch.dict("os.environ", {}, clear=True),
        ):
            self.service.generate("una scena", consent_to_external_processing=True)
        self.service.save_profile({"body": "corporatura media", "face": "viso ovale"})
        self.service.save_reference(PNG, "image/png")

    def test_replicate_generation_uses_reference_seed_and_saves_image(self):
        ConfigStore(self.root).update_current_state(
            {"flags": {"visual_generation_enabled": True}}, allow_visual_generation=True
        )
        self.service.save_profile({"body": "corporatura media", "face": "viso ovale", "fixed_seed": 1234})
        self.service.save_reference(PNG, "image/png")
        calls = []
        clients = []
        from agent_memory.visual import ReplicateBackend

        def fake_run(model, *, input):
            calls.append((model, input.copy(), input["input_image"].read()))
            return FakeFileOutput()

        class FakeClient:
            def __init__(self, api_token):
                clients.append(api_token)

            def run(self, model, *, input):
                return fake_run(model, input=input)

        from types import SimpleNamespace
        fake_module = SimpleNamespace(Client=FakeClient)
        with (
            patch.dict("os.environ", {"REPLICATE_API_TOKEN": "test-token"}),
            patch.dict("sys.modules", {"replicate": fake_module}),
            patch.object(ReplicateBackend, "_read_output_url", return_value=PNG),
        ):
            result = self.service.generate("in una biblioteca", consent_to_external_processing=True)
        self.assertEqual(result["seed"], 1234)
        self.assertEqual(clients, ["test-token"])
        self.assertEqual(calls[0][0], "black-forest-labs/flux-kontext-pro")
        self.assertEqual(calls[0][1]["seed"], 1234)
        self.assertTrue(calls[0][2].startswith(bytes.fromhex("ffd8")))
        self.assertIn("corporatura media", calls[0][1]["prompt"])
        self.assertIn("biblioteca", calls[0][1]["prompt"])
        saved = self.root / "files" / result["path"]
        self.assertTrue(saved.read_bytes().startswith(bytes.fromhex("89504e470d0a1a0a")))
        metadata = json.loads(saved.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["seed"], 1234)
        self.assertEqual(self.service.status()["generated_images"][0]["path"], result["path"])

    def test_output_url_requires_replicate_delivery_https(self):
        from agent_memory.visual import ReplicateBackend

        with patch("urllib.request.urlopen") as opener:
            with self.assertRaises(ConfigError):
                ReplicateBackend._read_output_url("https://attacker.example/image.png")
            opener.assert_not_called()

    def test_output_url_download_is_bounded_and_data_uri_decoded(self):
        from agent_memory.visual import ReplicateBackend

        self.assertEqual(
            ReplicateBackend._read_output_url("data:image/png;base64," + base64.b64encode(PNG).decode()),
            PNG,
        )
        self.assertEqual(MAX_GENERATED_BYTES, 32 * 1024 * 1024)

        class Response:
            def __init__(self):
                self.status = 200
                self.headers = {"Content-Length": str(MAX_GENERATED_BYTES + 1)}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with (
            patch("urllib.request.build_opener") as build_opener,
            self.assertRaises(ConfigError),
        ):
            build_opener.return_value.open.return_value = Response()
            ReplicateBackend._read_output_url("https://replicate.delivery/image.png")

    def test_only_one_generation_runs_for_a_profile(self):
        ConfigStore(self.root).update_current_state(
            {"flags": {"visual_generation_enabled": True}}, allow_visual_generation=True
        )
        self.service.save_profile({"body": "corporatura media"})
        self.service.save_reference(PNG, "image/png")
        entered = threading.Event()
        release = threading.Event()

        class BlockingBackend:
            def generate(self, _prompt, _image_path, _seed, *, token):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test generation timed out")
                return PNG

        first = VisualService(self.root, backend=BlockingBackend())
        second = VisualService(self.root, backend=BlockingBackend())
        results = []

        def run_first():
            results.append(
                first.generate("prima scena", consent_to_external_processing=True)
            )

        worker = threading.Thread(target=run_first)
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "test-token"}):
            worker.start()
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaisesRegex(ValidationError, "già in corso"):
                    second.generate("seconda scena", consent_to_external_processing=True)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)

    def test_token_file_is_used_only_with_restricted_permissions(self):
        ConfigStore(self.root).update_current_state(
            {"flags": {"visual_generation_enabled": True}}, allow_visual_generation=True
        )
        self.service.save_profile({"body": "corporatura media"})
        self.service.save_reference(PNG, "image/png")
        token_file = self.root / "data" / "secrets" / "replicate_token"
        token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("r8_local_file_token_123456\n")

        with patch.dict("os.environ", {}, clear=True):
            status = self.service.status()
            self.assertTrue(status["provider_configured"])
            self.assertEqual(status["provider_source"], "file")

        token_file.chmod(0o644)
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(self.service.status()["provider_configured"])
            with self.assertRaisesRegex(ConfigError, "chmod 600"):
                self.service.generate("una scena", consent_to_external_processing=True)

    def test_token_file_symlink_is_refused(self):
        secrets_dir = self.root / "data" / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.root / "token.txt"
        target.write_text("r8_symlink_token_123456\n", encoding="utf-8")
        target.chmod(0o600)
        (secrets_dir / "replicate_token").symlink_to(target)

        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(self.service.status()["provider_configured"])

    def test_environment_token_takes_precedence_over_the_file(self):
        secrets_dir = self.root / "data" / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        token_file = secrets_dir / "replicate_token"
        token_file.write_text("r8_file_token_123456\n", encoding="utf-8")
        token_file.chmod(0o600)

        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_env_token_123456"}):
            self.assertEqual(self.service.status()["provider_source"], "environment")
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(self.service.status()["provider_source"], "file")

    def test_generated_images_can_be_deleted_one_by_one(self):
        first = self.service._store_output(PNG, "prima scena", 11)
        second = self.service._store_output(PNG, "seconda scena", 12)
        self.assertEqual(len(self.service.status()["generated_images"]), 2)

        result = self.service.delete_image(first["id"])

        self.assertTrue(result["removed"])
        self.assertEqual([item["id"] for item in result["generated_images"]], [second["id"]])
        self.assertFalse((self.root / "files" / first["path"]).exists())
        self.assertFalse((self.root / "files" / first["path"]).with_suffix(".json").exists())
        with self.assertRaises(ConfigError):
            self.service.delete_image(first["id"])
        with self.assertRaises(ValidationError):
            self.service.delete_image("../../config/identity")

    def test_provider_check_records_origin_and_result(self):
        with (
            patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_status_token_123456"}),
            patch("agent_memory.visual_token.check_token", return_value={
                "valid": True, "status": 200, "username": "demo",
            }) as checker,
        ):
            status = self.service.check_provider()

        checker.assert_called_once_with("r8_status_token_123456")
        self.assertTrue(status["provider_checked_at"])
        self.assertTrue(status["provider_valid"])
        self.assertEqual(status["provider_username"], "demo")
        self.assertEqual(status["provider_source"], "environment")
        self.assertNotIn("r8_status_token_123456", json.dumps(status))
        recorded = self.root / "data" / "visual" / "token_status.json"
        self.assertEqual(recorded.stat().st_mode & 0o777, 0o600)

    def test_status_never_exposes_provider_token(self):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "never-return-this"}):
            status = self.service.status()
        self.assertTrue(status["provider_configured"])
        self.assertNotIn("never-return-this", json.dumps(status))


if __name__ == "__main__":
    unittest.main()
