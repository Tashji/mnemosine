"""Test del browser controllato e della whitelist."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agent_memory import browser_login
from agent_memory.browser import (
    ACTION_TIMEOUT_MS,
    READ_ATTEMPTS,
    READ_RETRY_DELAY_SECONDS,
    READY_TIMEOUT_MS,
    BrowserController,
    BrowserPolicy,
    PlaywrightBackend,
    _call_with_navigation_retry,
    _element_info,
    _is_transient_navigation_error,
    _save_storage_state,
    _storage_state,
)
from agent_memory.errors import (
    BrowserBlockedError,
    BrowserBusyError,
    BrowserError,
    BrowserUnavailableError,
    ConfigError,
    ValidationError,
)

PUBLIC_IP = "93.184.216.34"


def public_resolver(_host: str) -> list[str]:
    return [PUBLIC_IP]


class FakeBackend:
    def __init__(
        self,
        *,
        final_url: str | None = None,
        title: str = "Pagina di prova",
        text: str = "Contenuto della pagina",
        links: list[dict[str, str]] | None = None,
        elements: list[dict[str, Any]] | None = None,
        click_error: Exception | None = None,
    ) -> None:
        self.final_url = final_url
        self.title_text = title
        self.page_text = text
        self.link_items = links or []
        self.element_items = elements if elements is not None else [
            {
                "tag": "input",
                "role": "",
                "type": "search",
                "label": "Cerca",
                "value": "",
                "multiline": False,
            },
            {
                "tag": "div",
                "role": "textbox",
                "type": "",
                "label": "Scrivi",
                "value": "",
                "multiline": True,
            },
        ]
        self.click_error = click_error
        self.url = ""
        self.opened: list[str] = []
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.clicked_index: list[int] = []
        self.filled_index: list[tuple[int, str]] = []
        self.pressed: list[tuple[str, str | None, int | None]] = []
        self.waited: list[tuple[str | None, str | None, int | None]] = []
        self.elements_count = 0
        self.closed = False

    def open(self, url: str) -> None:
        self.opened.append(url)
        self.url = self.final_url or url

    def title(self) -> str:
        return self.title_text

    def text(self) -> str:
        return self.page_text

    def click(self, selector: str) -> None:
        if self.click_error is not None:
            raise self.click_error
        self.clicked.append(selector)

    def fill(self, selector: str, value: str) -> None:
        self.filled.append((selector, value))

    def elements(self, limit: int) -> dict[str, Any]:
        items = [
            dict(item, index=position + 1)
            for position, item in enumerate(self.element_items[:limit])
        ]
        self.elements_count = len(items)
        return {"elements": items, "total": len(self.element_items)}

    def click_element(self, index: int) -> None:
        self.clicked_index.append(index)

    def fill_element(self, index: int, value: str) -> None:
        self.filled_index.append((index, value))

    def press(
        self,
        key: str,
        *,
        selector: str | None = None,
        index: int | None = None,
    ) -> None:
        self.pressed.append((key, selector, index))

    def wait(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self.waited.append((selector, text, timeout_ms))

    def links(self) -> list[dict[str, str]]:
        return list(self.link_items)

    def screenshot(self, path: Path) -> None:
        path.write_bytes(b"png")

    def close(self) -> None:
        self.closed = True


def make_policy(
    origins: list[str] | None = None,
    *,
    enabled: bool = True,
    interaction: bool = False,
    service_workers: bool = False,
    user_agent: str | None = None,
    resolver=public_resolver,
) -> BrowserPolicy:
    return BrowserPolicy(
        origins if origins is not None else ["https://example.com"],
        enabled=enabled,
        interaction_enabled=interaction,
        interaction_origins=["https://example.com"] if interaction else [],
        service_workers=service_workers,
        user_agent=user_agent,
        resolver=resolver,
    )


class BrowserPolicyTests(unittest.TestCase):
    def test_existing_global_flag_does_not_grant_origin_consent(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(policy=BrowserPolicy(
            ["https://example.com"], enabled=True, interaction_enabled=True,
            resolver=public_resolver,
        ), backend=backend)
        controller.open("https://example.com")
        for action in (lambda: controller.click("button"), lambda: controller.fill("input", "test"), lambda: controller.press("Enter")):
            with self.assertRaises(BrowserBlockedError):
                action()
        self.assertEqual(backend.clicked, [])
        self.assertEqual(backend.filled, [])
        self.assertEqual(backend.pressed, [])
        self.assertTrue(controller.snapshot())

    def test_consent_is_exact_and_never_bypasses_navigation_whitelist(self) -> None:
        policy = BrowserPolicy(["https://*.example.com"], enabled=True,
            interaction_enabled=True, interaction_origins=["https://chat.example.com"], resolver=public_resolver)
        policy.assert_interaction_allowed("https://chat.example.com/path")
        with self.assertRaises(BrowserBlockedError):
            policy.assert_interaction_allowed("https://other.example.com")
        with self.assertRaises(BrowserBlockedError):
            policy.assert_interaction_allowed("https://example.com")
        with self.assertRaises(ConfigError):
            BrowserPolicy(interaction_origins=["https://*.example.com"])

    def test_allows_exact_https_origin(self) -> None:
        policy = make_policy()
        self.assertTrue(policy.is_allowed("https://example.com/una/pagina?q=1"))
        self.assertTrue(policy.is_allowed("https://example.com:443/"))
        self.assertEqual(policy.assert_allowed("https://example.com"), "https://example.com")

    def test_rejects_non_https_and_unsafe_targets(self) -> None:
        policy = make_policy()
        blocked = [
            "http://example.com",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "https://localhost/",
            "https://127.0.0.1/",
            "https://[::1]/",
            "https://10.0.0.1/",
            "https://example.local/",
            "https://user:password@example.com/",
            "https://example.com:8443/",
            "https://example.com@evil.test/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                self.assertFalse(policy.is_allowed(url))

    def test_wildcard_matches_subdomains_only(self) -> None:
        policy = make_policy(["https://*.example.com"])
        self.assertTrue(policy.is_allowed("https://forum.example.com"))
        self.assertTrue(policy.is_allowed("https://a.b.example.com/path"))
        self.assertFalse(policy.is_allowed("https://example.com"))
        self.assertFalse(policy.is_allowed("https://evilexample.com"))

    def test_private_dns_answer_is_blocked(self) -> None:
        policy = make_policy(
            ["https://rebind.example"],
            resolver=lambda _host: ["127.0.0.1"],
        )
        self.assertFalse(policy.is_allowed("https://rebind.example/"))

    def test_public_dns_answer_is_allowed(self) -> None:
        policy = make_policy(
            ["https://public.example"],
            resolver=lambda _host: ["93.184.216.34", "2606:2800:220:1::1"],
        )
        self.assertTrue(policy.is_allowed("https://public.example/"))

    def test_empty_allowlist_blocks_everything(self) -> None:
        policy = make_policy([], enabled=True)
        self.assertFalse(policy.is_allowed("https://example.com"))
        with self.assertRaises(BrowserBlockedError):
            policy.assert_allowed("https://example.com")

    def test_disabled_policy_raises_unavailable(self) -> None:
        policy = make_policy(enabled=False)
        with self.assertRaises(BrowserUnavailableError):
            policy.assert_allowed("https://example.com")

    def test_interaction_requires_explicit_enable(self) -> None:
        policy = make_policy(interaction=False)
        with self.assertRaises(BrowserBlockedError):
            policy.assert_interaction_allowed("https://example.com")

        enabled = make_policy(interaction=True)
        self.assertEqual(
            enabled.assert_interaction_allowed("https://example.com"),
            "https://example.com",
        )

    def test_websocket_requires_wss_and_whitelist(self) -> None:
        policy = make_policy()
        self.assertTrue(policy.is_allowed_websocket("wss://example.com/live"))
        self.assertFalse(policy.is_allowed_websocket("ws://example.com/live"))
        self.assertFalse(policy.is_allowed_websocket("wss://socket.test/live"))
        self.assertFalse(policy.is_allowed_websocket("wss://localhost/live"))

    def test_config_missing_means_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = BrowserPolicy.from_root(Path(tmp))
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.allowed_origins, ())

    def test_config_loads_browser_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "integrations.json").write_text(
                json.dumps(
                    {
                        "browser": {
                            "enabled": True,
                            "interaction_enabled": False,
                            "allowed_origins": ["https://example.com"],
                            "navigation_timeout_seconds": 15,
                        }
                    }
                ),
                encoding="utf-8",
            )
            policy = BrowserPolicy.from_root(root)
        self.assertTrue(policy.enabled)
        self.assertFalse(policy.interaction_enabled)
        self.assertEqual(policy.allowed_origins, ("https://example.com",))
        self.assertEqual(policy.timeout_seconds, 15)

    def test_config_sets_session_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "integrations.json").write_text(
                json.dumps(
                    {
                        "browser": {
                            "enabled": True,
                            "allowed_origins": ["https://example.com"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            policy = BrowserPolicy.from_root(root)
        self.assertEqual(
            policy.session_path, root / "data" / "browser" / "session.json"
        )

    def test_config_rejects_http_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "integrations.json").write_text(
                json.dumps(
                    {
                        "browser": {
                            "enabled": True,
                            "allowed_origins": ["http://example.com"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                BrowserPolicy.from_root(root)

    def test_config_rejects_invalid_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "integrations.json").write_text(
                json.dumps({"browser": {"enabled": "yes"}}),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                BrowserPolicy.from_root(root)

    def test_service_workers_are_blocked_by_default(self) -> None:
        policy = make_policy()
        self.assertFalse(policy.service_workers)
        self.assertEqual(policy.service_worker_mode, "block")

    def test_config_enables_service_workers_only_on_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "integrations.json").write_text(
                json.dumps(
                    {
                        "browser": {
                            "enabled": True,
                            "allowed_origins": ["https://example.com"],
                            "service_workers": True,
                        }
                    }
                ),
                encoding="utf-8",
            )
            policy = BrowserPolicy.from_root(root)
        self.assertTrue(policy.service_workers)
        self.assertEqual(policy.service_worker_mode, "allow")

    def test_user_agent_defaults_to_browser_value(self) -> None:
        self.assertIsNone(make_policy().user_agent)

    def test_config_reads_user_agent(self) -> None:
        agent = "Mozilla/5.0 (X11; Linux x86_64) Chrome/156.0.0.0"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "integrations.json").write_text(
                json.dumps(
                    {
                        "browser": {
                            "enabled": True,
                            "allowed_origins": ["https://example.com"],
                            "user_agent": f"  {agent}  ",
                        }
                    }
                ),
                encoding="utf-8",
            )
            policy = BrowserPolicy.from_root(root)
        self.assertEqual(policy.user_agent, agent)

    def test_config_rejects_invalid_user_agent(self) -> None:
        invalid: list[object] = [42, ["x"], "   ", "Chrome\nSafari", "x" * 513]
        for value in invalid:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "config").mkdir()
                (root / "config" / "integrations.json").write_text(
                    json.dumps({"browser": {"user_agent": value}}),
                    encoding="utf-8",
                )
                with self.assertRaises(ConfigError):
                    BrowserPolicy.from_root(root)

    def test_config_rejects_non_boolean_service_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "integrations.json").write_text(
                json.dumps({"browser": {"service_workers": "allow"}}),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                BrowserPolicy.from_root(root)


class BrowserControllerTests(unittest.TestCase):
    def test_blocks_before_opening_backend(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(policy=make_policy(), backend=backend)
        with self.assertRaises(BrowserBlockedError):
            controller.open("https://evil.test")
        self.assertEqual(backend.opened, [])

    def test_detects_redirect_outside_whitelist(self) -> None:
        backend = FakeBackend(final_url="https://evil.test/")
        controller = BrowserController(policy=make_policy(), backend=backend)
        with self.assertRaises(BrowserBlockedError):
            controller.open("https://example.com")
        self.assertEqual(backend.opened, ["https://example.com"])

    def test_open_returns_truncated_snapshot(self) -> None:
        backend = FakeBackend(title="Titolo", text="abcdefghij")
        controller = BrowserController(policy=make_policy(), backend=backend)
        result = controller.open("https://example.com", max_chars=5)
        self.assertEqual(result["url"], "https://example.com")
        self.assertEqual(result["title"], "Titolo")
        self.assertEqual(result["text"], "abcde")
        self.assertTrue(result["truncated"])

    def test_read_before_open_is_rejected(self) -> None:
        controller = BrowserController(policy=make_policy(), backend=FakeBackend())
        with self.assertRaises(BrowserUnavailableError):
            controller.snapshot()

    def test_interaction_is_gated(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(
            policy=make_policy(interaction=False), backend=backend
        )
        controller.open("https://example.com")
        with self.assertRaises(BrowserBlockedError):
            controller.click("#send")
        with self.assertRaises(BrowserBlockedError):
            controller.fill("#message", "ciao")
        self.assertEqual(backend.clicked, [])
        self.assertEqual(backend.filled, [])

    def test_interaction_works_when_enabled(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(
            policy=make_policy(interaction=True), backend=backend
        )
        controller.open("https://example.com")
        clicked = controller.click("#send")
        filled = controller.fill("#message", "ciao")
        self.assertEqual(clicked["url"], "https://example.com")
        self.assertEqual(filled["url"], "https://example.com")
        self.assertEqual(backend.clicked, ["#send"])
        self.assertEqual(backend.filled, [("#message", "ciao")])

    def test_selector_and_text_limits(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(
            policy=make_policy(interaction=True), backend=backend
        )
        controller.open("https://example.com")
        with self.assertRaises(ValidationError):
            controller.click("")
        with self.assertRaises(ValidationError):
            controller.fill("#message", "x" * 4001)
        with self.assertRaises(ValidationError):
            controller.snapshot(max_chars=0)

    def test_blocked_error_lists_allowed_origins(self) -> None:
        policy = make_policy(["https://example.com", "https://forum.example.org"])
        with self.assertRaises(BrowserBlockedError) as caught:
            policy.assert_allowed("https://evil.test")
        message = str(caught.exception)
        self.assertIn("Origini consentite", message)
        self.assertIn("https://example.com", message)
        self.assertIn("https://forum.example.org", message)

        empty = make_policy([])
        with self.assertRaises(BrowserBlockedError) as caught_empty:
            empty.assert_allowed("https://example.com")
        self.assertIn("Nessuna origine", str(caught_empty.exception))

    def test_links_keep_only_allowed_hosts_and_are_numbered(self) -> None:
        backend = FakeBackend(
            links=[
                {"text": "Forum", "url": "https://example.com/forum"},
                {"text": "Tracciante", "url": "https://evil.test/pixel"},
                {"text": "  Duplicato  ", "url": "https://example.com/forum#inizio"},
                {"text": "", "url": "https://example.com/documenti"},
            ]
        )
        controller = BrowserController(policy=make_policy(), backend=backend)
        controller.open("https://example.com")
        result = controller.links()
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["blocked_count"], 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(
            [link["index"] for link in result["links"]], [1, 2]
        )
        self.assertEqual(result["links"][0]["text"], "Forum")
        self.assertEqual(result["links"][1]["text"], "https://example.com/documenti")

    def test_links_require_an_open_page(self) -> None:
        controller = BrowserController(policy=make_policy(), backend=FakeBackend())
        with self.assertRaises(BrowserUnavailableError):
            controller.links()
        with self.assertRaises(ValidationError):
            controller.links(limit=0)

    def test_open_link_requires_links_first(self) -> None:
        controller = BrowserController(policy=make_policy(), backend=FakeBackend())
        controller.open("https://example.com")
        with self.assertRaises(BrowserUnavailableError) as caught:
            controller.open_link(1)
        self.assertIn("browser_links", str(caught.exception))

    def test_open_link_validates_index(self) -> None:
        backend = FakeBackend(
            links=[{"text": "Uno", "url": "https://example.com/uno"}]
        )
        controller = BrowserController(policy=make_policy(), backend=backend)
        controller.open("https://example.com")
        controller.links()
        with self.assertRaises(ValidationError) as caught:
            controller.open_link(2)
        self.assertIn("1 link", str(caught.exception))
        with self.assertRaises(ValidationError):
            controller.open_link(True)  # type: ignore[arg-type]

    def test_open_link_navigates_and_updates_history(self) -> None:
        backend = FakeBackend(
            links=[{"text": "Forum", "url": "https://example.com/forum"}]
        )
        controller = BrowserController(policy=make_policy(), backend=backend)
        controller.open("https://example.com")
        controller.links()
        page = controller.open_link(1, max_chars=4)
        self.assertEqual(page["text"], "Cont")
        self.assertEqual(backend.opened, ["https://example.com", "https://example.com/forum"])
        self.assertEqual(controller.current_url, "https://example.com/forum")
        self.assertTrue(controller.status()["can_go_back"])

    def test_navigation_invalidates_previous_links(self) -> None:
        backend = FakeBackend(
            links=[{"text": "Forum", "url": "https://example.com/forum"}]
        )
        controller = BrowserController(policy=make_policy(), backend=backend)
        controller.open("https://example.com")
        controller.links()
        controller.open("https://example.com/altra")
        with self.assertRaises(BrowserUnavailableError) as caught:
            controller.open_link(1)
        self.assertIn("browser_links", str(caught.exception))

    def test_back_returns_to_previous_page(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(policy=make_policy(), backend=backend)
        controller.open("https://example.com")
        controller.open("https://example.com/forum")
        page = controller.back()
        self.assertEqual(page["url"], "https://example.com")
        self.assertEqual(
            backend.opened,
            ["https://example.com", "https://example.com/forum", "https://example.com"],
        )
        self.assertFalse(controller.status()["can_go_back"])

    def test_back_without_history_is_explained(self) -> None:
        controller = BrowserController(policy=make_policy(), backend=FakeBackend())
        controller.open("https://example.com")
        with self.assertRaises(BrowserUnavailableError) as caught:
            controller.back()
        self.assertIn("browser_open", str(caught.exception))

    def test_click_failure_suggests_links(self) -> None:
        backend = FakeBackend(click_error=BrowserError("Click non riuscito: timeout"))
        controller = BrowserController(
            policy=make_policy(interaction=True), backend=backend
        )
        controller.open("https://example.com")
        with self.assertRaises(BrowserError) as caught:
            controller.click("a.forum")
        self.assertIn("browser_links", str(caught.exception))

    def test_click_busy_is_not_treated_as_a_bad_selector(self) -> None:
        backend = FakeBackend(
            click_error=BrowserBusyError(
                "Click non riuscito: la pagina non è pronta, riprova tra poco."
            )
        )
        controller = BrowserController(
            policy=make_policy(interaction=True), backend=backend
        )
        controller.open("https://example.com")
        with self.assertRaises(BrowserBusyError) as caught:
            controller.click("a.forum")
        self.assertNotIn("browser_links", str(caught.exception))

    def test_elements_are_numbered_and_truncated(self) -> None:
        backend = FakeBackend(
            elements=[
                {"tag": "input", "label": "Cerca"},
                {"tag": "button", "label": "Invia"},
                {"tag": "div", "label": "Scrivi"},
            ]
        )
        controller = BrowserController(policy=make_policy(), backend=backend)
        with self.assertRaises(BrowserUnavailableError):
            controller.elements()
        controller.open("https://example.com")
        result = controller.elements(limit=2)
        self.assertEqual(result["url"], "https://example.com")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["total"], 3)
        self.assertTrue(result["truncated"])
        self.assertEqual([item["index"] for item in result["elements"]], [1, 2])

    def test_elements_validate_limit(self) -> None:
        controller = BrowserController(policy=make_policy(), backend=FakeBackend())
        controller.open("https://example.com")
        for limit in (0, 61, True):
            with self.subTest(limit=limit), self.assertRaises(ValidationError):
                controller.elements(limit=limit)

    def test_mutations_accept_an_index(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(
            policy=make_policy(interaction=True), backend=backend
        )
        controller.open("https://example.com")
        controller.elements()
        controller.click(index=2)
        controller.fill(index=1, value="ciao")
        self.assertEqual(backend.clicked_index, [2])
        self.assertEqual(backend.filled_index, [(1, "ciao")])

    def test_selector_and_index_are_exclusive(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(
            policy=make_policy(interaction=True), backend=backend
        )
        controller.open("https://example.com")
        with self.assertRaises(ValidationError):
            controller.click("#send", index=1)
        with self.assertRaises(ValidationError) as caught:
            controller.click()
        self.assertIn("browser_elements", str(caught.exception))
        with self.assertRaises(ValidationError):
            controller.fill(value="ciao")
        with self.assertRaises(ValidationError):
            controller.fill("#message", "ciao", index=1)
        with self.assertRaises(ValidationError):
            controller.click(index=0)
        with self.assertRaises(ValidationError):
            controller.click(index=True)  # type: ignore[arg-type]

    def test_press_validates_keys_and_records_target(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(
            policy=make_policy(interaction=True), backend=backend
        )
        controller.open("https://example.com")
        controller.press("Enter")
        controller.press("Control+Enter", index=2)
        controller.press("Escape", selector="#chiudi")
        self.assertEqual(
            backend.pressed,
            [
                ("Enter", None, None),
                ("Control+Enter", None, 2),
                ("Escape", "#chiudi", None),
            ],
        )
        for key in ("enter", "F5", "Invio", "", "a", "x" * 500):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                controller.press(key)
        with self.assertRaises(ValidationError):
            controller.press("Enter", selector="#campo", index=1)

    def test_press_requires_interaction(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(
            policy=make_policy(interaction=False), backend=backend
        )
        controller.open("https://example.com")
        with self.assertRaises(BrowserBlockedError):
            controller.press("Enter")
        self.assertEqual(backend.pressed, [])

    def test_wait_accepts_selector_or_text(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(policy=make_policy(), backend=backend)
        controller.open("https://example.com")
        controller.wait(selector="#chat")
        controller.wait(text="  Inviato  ", timeout_seconds=2)
        self.assertEqual(
            backend.waited,
            [("#chat", None, None), (None, "Inviato", 2000)],
        )
        with self.assertRaises(ValidationError):
            controller.wait()
        with self.assertRaises(ValidationError):
            controller.wait(text="  ")
        with self.assertRaises(ValidationError):
            controller.wait(text="x" * 301)
        with self.assertRaises(ValidationError):
            controller.wait(selector="#a", timeout_seconds=0)
        with self.assertRaises(ValidationError):
            controller.wait(selector="#a", timeout_seconds=True)  # type: ignore[arg-type]

    def test_status_exposes_session_and_elements(self) -> None:
        controller = BrowserController(policy=make_policy(), backend=FakeBackend())
        controller.open("https://example.com")
        controller.elements()
        status = controller.status()
        self.assertEqual(status["elements_count"], 2)
        self.assertFalse(status["session_saved"])
        self.assertEqual(status["session_file"], "data/browser/session.json")
        self.assertEqual(status["service_workers"], "block")
        self.assertIsNone(status["service_workers_warning"])
        self.assertIsNone(status["user_agent"])

    def test_status_exposes_user_agent(self) -> None:
        agent = "Mozilla/5.0 (X11; Linux x86_64) Chrome/156.0.0.0"
        controller = BrowserController(
            policy=make_policy(user_agent=agent), backend=FakeBackend()
        )
        self.assertEqual(controller.status()["user_agent"], agent)

    def test_status_warns_when_service_workers_are_allowed(self) -> None:
        controller = BrowserController(
            policy=make_policy(service_workers=True), backend=FakeBackend()
        )
        status = controller.status()
        self.assertEqual(status["service_workers"], "allow")
        self.assertIn(
            "non passano dal filtro delle origini",
            status["service_workers_warning"],
        )

    def test_close_releases_backend(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(policy=make_policy(), backend=backend)
        controller.open("https://example.com")
        controller.close()
        self.assertTrue(backend.closed)
        self.assertIsNone(controller.current_url)


class BrowserSessionTests(unittest.TestCase):
    def test_missing_session_is_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = BrowserPolicy([], session_path=Path(tmp) / "session.json")
            self.assertIsNone(_storage_state(policy))
            self.assertFalse(_save_storage_state(policy, None))

    def test_session_is_saved_with_restricted_permissions(self) -> None:
        class FakeContext:
            def storage_state(self, path: str) -> None:
                Path(path).write_text("{}", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "data" / "browser" / "session.json"
            policy = BrowserPolicy([], session_path=target)
            self.assertTrue(_save_storage_state(policy, FakeContext()))
            self.assertTrue(target.is_file())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(_storage_state(policy), str(target))

    def test_session_without_path_is_not_saved(self) -> None:
        class FakeContext:
            def storage_state(self, path: str) -> None:
                raise AssertionError("non deve salvare")

        self.assertFalse(_save_storage_state(BrowserPolicy([]), FakeContext()))

    def test_save_failure_is_reported(self) -> None:
        class BrokenContext:
            def storage_state(self, path: str) -> None:
                raise RuntimeError("disco pieno")

        with tempfile.TemporaryDirectory() as tmp:
            policy = BrowserPolicy([], session_path=Path(tmp) / "session.json")
            self.assertFalse(_save_storage_state(policy, BrokenContext()))

    def test_element_info_skips_unusable_handles(self) -> None:
        class Handle:
            def __init__(
                self,
                *,
                visible: bool = True,
                info: Any = None,
                fail: bool = False,
            ) -> None:
                self.visible = visible
                self.info = info
                self.fail = fail

            def is_visible(self) -> bool:
                return self.visible

            def is_enabled(self) -> bool:
                return True

            def evaluate(self, _script: str) -> Any:
                if self.fail:
                    raise RuntimeError("elemento staccato")
                return self.info

        self.assertIsNone(_element_info(Handle(visible=False)))
        self.assertIsNone(_element_info(Handle(fail=True)))
        self.assertIsNone(_element_info(Handle(info=[1, 2])))
        self.assertEqual(_element_info(Handle(info={"tag": "input"})), {"tag": "input"})


class BrowserLoginTests(unittest.TestCase):
    def test_origin_without_scheme_becomes_https(self) -> None:
        self.assertEqual(
            browser_login._as_url("web.whatsapp.com"),
            "https://web.whatsapp.com",
        )
        self.assertEqual(
            browser_login._as_url(" https://discord.com "),
            "https://discord.com",
        )

    def test_check_reports_missing_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "integrations.json").write_text(
                json.dumps(
                    {
                        "browser": {
                            "enabled": True,
                            "allowed_origins": ["https://example.com"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = browser_login.main(["--root", str(root), "--check"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["session_file"], "data/browser/session.json")
        self.assertFalse(payload["session_saved"])
        self.assertEqual(payload["service_workers"], "block")
        self.assertIsNone(payload["service_workers_warning"])
        self.assertIsNone(payload["user_agent"])

    def test_disabled_browser_refuses_to_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                code = browser_login.main(["--root", tmp])
        self.assertEqual(code, 1)
        self.assertIn("ERRORE", buffer.getvalue())


class BrowserReloadTests(unittest.TestCase):
    """Comportamento quando la pagina cambia documento durante una lettura."""

    def test_navigation_errors_are_recognized(self) -> None:
        for message in (
            "Execution context was destroyed.",
            "Cannot find context with specified id",
            "Element is not attached to the DOM",
        ):
            with self.subTest(message=message):
                self.assertTrue(
                    _is_transient_navigation_error(RuntimeError(message))
                )
        self.assertFalse(
            _is_transient_navigation_error(RuntimeError("selettore inesistente"))
        )

    def test_retries_once_after_a_navigation(self) -> None:
        calls: list[int] = []
        sleeps: list[float] = []

        def call() -> str:
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                raise RuntimeError("Execution context was destroyed.")
            return "ok"

        result = _call_with_navigation_retry(call, sleep=sleeps.append)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [READ_RETRY_DELAY_SECONDS])

    def test_other_errors_are_not_retried(self) -> None:
        calls: list[int] = []

        def call() -> None:
            calls.append(len(calls) + 1)
            raise RuntimeError("selettore inesistente")

        with self.assertRaises(RuntimeError):
            _call_with_navigation_retry(call, sleep=lambda _seconds: None)
        self.assertEqual(len(calls), 1)

    def test_page_still_reloading_becomes_an_explicit_error(self) -> None:
        class ChangingPage:
            def __init__(self) -> None:
                self.waits = 0

            def wait_for_load_state(self, _state: str, timeout: int) -> None:
                self.waits += 1
                raise RuntimeError("Execution context was destroyed.")

        backend = object.__new__(PlaywrightBackend)
        page = ChangingPage()
        backend._page = page
        with self.assertRaises(BrowserBusyError) as caught:
            backend._with_page(lambda _page: "x", message="Lettura")
        self.assertIn("ricaricando", str(caught.exception))
        self.assertEqual(page.waits, READ_ATTEMPTS)

    def test_settled_page_is_read_normally(self) -> None:
        class SettledPage:
            def wait_for_load_state(self, _state: str, timeout: int) -> None:
                return None

            def evaluate(self, _script: str) -> str:
                return "complete"

        backend = object.__new__(PlaywrightBackend)
        backend._page = SettledPage()
        self.assertEqual(
            backend._with_page(lambda _page: "letto", message="Lettura"),
            "letto",
        )

    def test_page_stuck_in_loading_is_reported(self) -> None:
        class LoadingPage:
            def wait_for_load_state(self, _state: str, timeout: int) -> None:
                return None

            def evaluate(self, _script: str) -> str:
                return "loading"

        backend = object.__new__(PlaywrightBackend)
        backend._page = LoadingPage()
        with (
            mock.patch("agent_memory.browser.READY_TIMEOUT_MS", 50),
            self.assertRaises(BrowserBusyError) as caught,
        ):
            backend._with_page(lambda _page: "x", message="Lettura")
        self.assertIn("caricamento", str(caught.exception))

    def test_text_reports_a_reload_instead_of_an_empty_page(self) -> None:
        class ChangingPage:
            def wait_for_load_state(self, _state: str, timeout: int) -> None:
                raise RuntimeError("Execution context was destroyed.")

            def evaluate(self, _script: str) -> str:
                raise RuntimeError("Execution context was destroyed.")

        backend = object.__new__(PlaywrightBackend)
        backend._page = ChangingPage()
        with self.assertRaises(BrowserBusyError):
            backend.text()

    def test_text_without_a_body_is_empty(self) -> None:
        class NoBodyPage:
            def wait_for_load_state(self, _state: str, timeout: int) -> None:
                return None

            def evaluate(self, script: str) -> str:
                return "complete" if script == "document.readyState" else ""

        backend = object.__new__(PlaywrightBackend)
        backend._page = NoBodyPage()
        self.assertEqual(backend.text(), "")


class BrowserActionTimeoutTests(unittest.TestCase):
    """Click e screenshot non devono attendere il timeout pieno della policy."""

    class TimeoutError(Exception):
        pass

    def make_backend(
        self,
        *,
        click_error: Exception | None = None,
        screenshot_error: Exception | None = None,
    ) -> tuple[PlaywrightBackend, dict[str, Any]]:
        calls: dict[str, Any] = {
            "waits": 0,
            "click": [],
            "fill": [],
            "press": [],
            "screenshot": [],
        }

        class Page:
            url = "https://example.com"

            def wait_for_load_state(self, _state: str, timeout: int) -> None:
                calls["waits"] += 1

            def evaluate(self, _script: str) -> str:
                return "complete"

            def click(self, selector: str, timeout: int | None = None) -> None:
                calls["click"].append((selector, timeout))
                if click_error is not None:
                    raise click_error

            def fill(
                self, selector: str, value: str, timeout: int | None = None
            ) -> None:
                calls["fill"].append((selector, value, timeout))

            def press(self, selector: str, key: str, timeout: int | None = None) -> None:
                calls["press"].append((selector, key, timeout))

            def screenshot(
                self,
                path: str,
                full_page: bool = False,
                timeout: int | None = None,
            ) -> None:
                calls["screenshot"].append((path, full_page, timeout))
                if screenshot_error is not None:
                    raise screenshot_error

        backend = object.__new__(PlaywrightBackend)
        backend._page = Page()
        return backend, calls

    def test_limit_is_the_same_as_the_read_limit(self) -> None:
        self.assertEqual(ACTION_TIMEOUT_MS, READY_TIMEOUT_MS)

    def test_click_waits_for_the_page_but_uses_the_short_limit(self) -> None:
        backend, calls = self.make_backend()
        backend.click("a.forum")
        self.assertEqual(calls["waits"], 1)
        self.assertEqual(calls["click"], [("a.forum", ACTION_TIMEOUT_MS)])

    def test_click_timeout_is_an_explicit_error(self) -> None:
        backend, _ = self.make_backend(
            click_error=self.TimeoutError("Timeout 3000ms exceeded.")
        )
        with self.assertRaises(BrowserBusyError) as caught:
            backend.click("a.forum")
        self.assertIn("pronta", str(caught.exception))
        self.assertIn("riprova", str(caught.exception))

    def test_fill_and_press_use_the_short_limit(self) -> None:
        backend, calls = self.make_backend()
        backend.fill("#cerca", "mnemosine")
        backend.press("Enter", selector="#cerca")
        self.assertEqual(calls["fill"], [("#cerca", "mnemosine", ACTION_TIMEOUT_MS)])
        self.assertEqual(calls["press"], [("#cerca", "Enter", ACTION_TIMEOUT_MS)])

    def test_screenshot_waits_for_the_page_and_uses_the_short_limit(self) -> None:
        backend, calls = self.make_backend()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "schermata.png"
            backend.screenshot(target)
        self.assertEqual(calls["waits"], 1)
        self.assertEqual(len(calls["screenshot"]), 1)
        path, full_page, timeout = calls["screenshot"][0]
        self.assertEqual(path, str(target))
        self.assertTrue(full_page)
        self.assertEqual(timeout, ACTION_TIMEOUT_MS)

    def test_screenshot_timeout_is_an_explicit_error(self) -> None:
        backend, _ = self.make_backend(
            screenshot_error=self.TimeoutError("Timeout 3000ms exceeded.")
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(BrowserBusyError) as caught,
        ):
            backend.screenshot(Path(tmp) / "schermata.png")
        self.assertIn("pronta", str(caught.exception))
        self.assertIn("riprova", str(caught.exception))


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = type("Request", (), {"url": url})()
        self.continued = False
        self.aborted = False

    def continue_(self) -> None:
        self.continued = True

    def abort(self) -> None:
        self.aborted = True


class FakeWebSocket:
    def __init__(self, url: str) -> None:
        self.url = url
        self.connected = False
        self.closed = False

    def connect_to_server(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True


class PlaywrightRouteTests(unittest.TestCase):
    def test_route_allows_inline_and_whitelisted_requests(self) -> None:
        backend = object.__new__(PlaywrightBackend)
        backend.policy = make_policy()
        for url in (
            "data:image/png;base64,AAAA",
            "blob:https://example.com/id",
            "https://example.com/app.js",
        ):
            route = FakeRoute(url)
            backend._handle_route(route)
            with self.subTest(url=url):
                self.assertTrue(route.continued)
                self.assertFalse(route.aborted)

    def test_route_blocks_external_request(self) -> None:
        backend = object.__new__(PlaywrightBackend)
        backend.policy = make_policy()
        route = FakeRoute("https://tracker.test/pixel")
        backend._handle_route(route)
        self.assertTrue(route.aborted)
        self.assertFalse(route.continued)

    def test_websocket_outside_whitelist_is_closed(self) -> None:
        backend = object.__new__(PlaywrightBackend)
        backend.policy = make_policy()
        socket = FakeWebSocket("wss://socket.test/live")
        backend._handle_websocket(socket)
        self.assertTrue(socket.closed)
        self.assertFalse(socket.connected)

    def test_websocket_inside_whitelist_connects(self) -> None:
        backend = object.__new__(PlaywrightBackend)
        backend.policy = make_policy()
        socket = FakeWebSocket("wss://example.com/live")
        backend._handle_websocket(socket)
        self.assertTrue(socket.connected)
        self.assertFalse(socket.closed)


if __name__ == "__main__":
    unittest.main()
