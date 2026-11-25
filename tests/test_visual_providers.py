"""Test dei provider immagini configurabili e dei loro endpoint."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agent_memory.config import ConfigStore
from agent_memory.errors import ConfigError, ValidationError
from agent_memory.visual import VisualService
from agent_memory.visual_providers import (
    api_key_path,
    load_provider,
    save_provider,
    validate_provider,
)
from tests.support import copy_profile
from tests.test_visual import PNG


class ProviderHandler(BaseHTTPRequestHandler):
    received: list[tuple[str, bytes]] = []

    def log_message(self, *_args):
        return

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/sdapi/v1/options":
            self._send({"sd_model_checkpoint": "demo.safetensors"})
        elif self.path == "/v1/models":
            self._send({"data": [{"id": "demo"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received.append((self.path, body))
        encoded = base64.b64encode(PNG).decode("ascii")
        if self.path == "/sdapi/v1/img2img":
            self._send({"images": [encoded]})
        elif self.path == "/v1/images/edits":
            self._send({"data": [{"b64_json": encoded}]})
        else:
            self._send({"error": "not found"}, 404)


class ProviderFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        copy_profile(self.root)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        ProviderHandler.received = []

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def _enable(self):
        ConfigStore(self.root).update_current_state(
            {"flags": {"visual_generation_enabled": True}},
            allow_visual_generation=True,
        )
        service = VisualService(self.root)
        service.save_profile({"body": "corporatura media", "face": "viso ovale"})
        service.save_reference(PNG, "image/png")
        return service


class LocalProviderTests(ProviderFixture):
    def test_local_provider_generates_from_local_api(self):
        save_provider(
            self.root,
            {
                "provider": "local",
                "base_url": self.base_url,
                "model": "demo.safetensors",
            },
        )
        service = self._enable()
        result = service.generate("in una biblioteca", consent_to_external_processing=True)
        self.assertTrue((self.root / "files" / result["path"]).is_file())
        metadata = json.loads(
            (self.root / "files" / result["path"]).with_suffix(".json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["model"], "demo.safetensors")
        self.assertEqual(ProviderHandler.received[0][0], "/sdapi/v1/img2img")
        payload = json.loads(ProviderHandler.received[0][1])
        self.assertIn("init_images", payload)
        status = service.status()
        self.assertEqual(status["provider_id"], "local")
        self.assertFalse(status["external"])
        self.assertTrue(status["provider_configured"])
        self.assertIsNone(status["price_per_image_usd"])

        checked = service.check_provider()
        self.assertTrue(checked["provider_valid"])
        self.assertEqual(checked["provider_detail"], "demo.safetensors")

    def test_local_provider_must_be_loopback(self):
        with self.assertRaises(ValidationError):
            validate_provider(
                self.root,
                {
                    "provider": "local",
                    "base_url": "http://192.168.1.20:7860",
                    "model": "",
                },
            )
        with self.assertRaises(ValidationError):
            validate_provider(
                self.root,
                {
                    "provider": "local",
                    "base_url": "https://example.com",
                    "model": "",
                },
            )

    def test_validation_does_not_write_before_profile_creation(self):
        missing = Path(self._tmp.name) / "nuovo"
        cleaned = validate_provider(
            missing,
            {
                "provider": "local",
                "base_url": "http://127.0.0.1:7860",
                "model": "",
            },
        )
        self.assertEqual(cleaned["provider"], "local")
        self.assertFalse((missing / "config" / "integrations.json").exists())


class OpenAIProviderTests(ProviderFixture):
    def test_openai_provider_uses_edits_and_stores_key_privately(self):
        secret = "sk-test-1234567890"
        save_provider(
            self.root,
            {
                "provider": "openai",
                "base_url": f"{self.base_url}/v1",
                "model": "gpt-image-1",
                "api_key": secret,
            },
        )
        key_file = api_key_path(self.root)
        self.assertEqual(key_file.read_text(encoding="utf-8").strip(), secret)
        if os.name != "nt":
            self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
        service = self._enable()
        result = service.generate("in un parco", consent_to_external_processing=True)
        self.assertTrue((self.root / "files" / result["path"]).is_file())
        self.assertEqual(ProviderHandler.received[0][0], "/v1/images/edits")
        self.assertIn(b"gpt-image-1", ProviderHandler.received[0][1])
        status = service.status()
        self.assertTrue(status["external"])
        self.assertEqual(status["provider_id"], "openai")
        self.assertNotIn(secret, json.dumps(status))
        checked = service.check_provider()
        self.assertTrue(checked["provider_valid"])

    def test_openai_requires_a_key(self):
        with self.assertRaises(ValidationError):
            save_provider(
                self.root,
                {
                    "provider": "openai",
                    "base_url": f"{self.base_url}/v1",
                    "model": "gpt-image-1",
                    "api_key": "",
                },
                allow_existing_secret=False,
            )

    def test_openai_remote_http_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_provider(
                self.root,
                {
                    "provider": "openai",
                    "base_url": "http://api.example.com/v1",
                    "model": "gpt-image-1",
                    "api_key": "sk-test-1234567890",
                },
            )

    def test_unknown_provider_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_provider(self.root, {"provider": "magic", "api_key": "x" * 20})


if __name__ == "__main__":
    unittest.main()
