"""Test del browser controllato e della whitelist."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_memory.browser import (
    BrowserController,
    BrowserPolicy,
    PlaywrightBackend,
)
from agent_memory.errors import (
    BrowserBlockedError,
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
        click_error: Exception | None = None,
    ) -> None:
        self.final_url = final_url
        self.title_text = title
        self.page_text = text
        self.link_items = links or []
        self.click_error = click_error
        self.url = ""
        self.opened: list[str] = []
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []
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
    resolver=public_resolver,
) -> BrowserPolicy:
    return BrowserPolicy(
        origins if origins is not None else ["https://example.com"],
        enabled=enabled,
        interaction_enabled=interaction,
        resolver=resolver,
    )


class BrowserPolicyTests(unittest.TestCase):
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

    def test_close_releases_backend(self) -> None:
        backend = FakeBackend()
        controller = BrowserController(policy=make_policy(), backend=backend)
        controller.open("https://example.com")
        controller.close()
        self.assertTrue(backend.closed)
        self.assertIsNone(controller.current_url)


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
