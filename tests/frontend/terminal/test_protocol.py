"""The WebSocket client and the pure helpers behind the terminal (package 8b): close codes to
user-facing states, input framing, copy for the Disconnected bar and checklist, the CSP for
same-origin ws:// (a real WebSocket, not the mock), and a refused token."""

from __future__ import annotations

from playwright.sync_api import expect

from tests.frontend.terminal.helpers import TermFactory, connect, panel, run, wait_until


def test_close_state(term: TermFactory) -> None:
    page = term()

    def state(code: int, err: str | None = None) -> str:
        body = f"m.closeState({code}, {('{code: ' + repr(err) + '}') if err else 'null'})"
        result: str = run(page, "ws/protocol.js", body)
        return result

    assert state(1000) == "done"
    assert state(1006) == "dropped"
    assert state(1001) == "dropped"
    assert state(4401) == "signed_out"
    assert state(4410) == "gone"
    assert state(4502) == "failed"
    assert state(1000, "cancelled") == "cancelled"
    assert state(1000, "hostkey_rejected") == "cancelled"
    assert state(4401, "unauthorized") == "signed_out"
    assert state(4401, "auth_timeout") == "signed_out"
    assert state(4410, "session_gone") == "gone"
    assert state(4409, "hostkey_changed") == "failed"
    assert state(4502, "timeout") == "failed"


def test_input_frames_are_at_most_64_kb(term: TermFactory) -> None:
    page = term()
    sizes = run(page, "ws/protocol.js", "m.inputFrames('x'.repeat(100000)).map((f) => f.length)")
    assert sizes == [65536, 34464]
    utf8 = run(page, "ws/protocol.js", "Array.from(m.inputFrames('é')[0])")
    assert utf8 == [0xC3, 0xA9]
    assert run(page, "ws/protocol.js", "m.socketUrl('/ws/terminal')").startswith("ws://127.0.0.1:")


def test_messages(term: TermFactory) -> None:
    page = term()
    m = "terminal/messages.js"
    assert run(page, m, "m.pasteLines('ls\\n')") == 1
    assert run(page, m, "m.pasteLines('a\\r\\nb\\rc\\n')") == 3
    assert run(page, m, "m.pasteLines('')") == 0
    assert run(page, m, "m.connectedFor(30000)") == "Connected just now"
    assert run(page, m, "m.connectedFor(12 * 60000)") == "Connected 12 min"
    assert run(page, m, "m.connectedFor(125 * 60000)") == "Connected 2 h 5 min"
    at = "new Date(2026, 10, 16, 14, 32)"
    assert run(page, m, f"m.disconnectedText('web-prod', 'closed', {at})") == {
        "title": "Disconnected from web-prod at 14:32",
        "body": "The remote host closed the connection. Press Enter or select Reconnect to open a new shell.",
    }
    assert run(page, m, f"m.disconnectedText('imaging-01', 'lost', {at})") == {
        "title": "Connection to imaging-01 lost at 14:32",
        "body": "No response for 90 seconds. Press Enter or select Reconnect to open a new shell.",
    }
    hop = "{index: 0, count: 2, name: 'build-box', host: '192.168.10.20', port: 22, role: 'jump'}"
    assert run(page, m, f"m.statusLine({{phase: 'auth', hop: {hop}}}).text") == "Reaching build-box…"
    target = "{index: 1, count: 2, name: 'web-prod', host: '192.168.10.42', port: 22, role: 'target'}"
    assert run(page, m, f"m.statusLine({{phase: 'connecting', hop: {target}}}).text") == "Reaching 192.168.10.42:22…"
    assert run(page, m, f"m.statusLine({{phase: 'auth', hop: {target}, method: 'publickey', key_name: 'id_ed25519'}}).text") == "Signing in with id_ed25519…"
    assert run(page, "ui/dialogs.js", "[m.ttlText(28800), m.ttlText(3600), m.ttlText(1800), m.ttlText(86400)]") == [
        "8 hours", "1 hour", "30 minutes", "1 day",
    ]
    assert run(page, "wizard/hostkey.js", "m.keygenCommand('rsa-sha2-512')") == "ssh-keygen -lf /etc/ssh/ssh_host_rsa_key.pub"
    assert run(page, "wizard/hostkey.js", "m.nameMatches('  WEB-prod ', 'web-prod')") is True
    assert run(page, "wizard/hostkey.js", "m.nameMatches('', '')") is False


def test_csp_allows_same_origin_ws_and_blocks_others(term: TermFactory) -> None:
    page = term(allow_errors=True)
    page.evaluate(
        """() => new Promise((resolve) => {
          const ws = new __pkWs.RealWebSocket(`ws://${location.host}/ws/terminal`);
          ws.addEventListener('error', () => resolve(true));
          ws.addEventListener('close', () => resolve(true));
        })"""
    )
    problems: list[str] = page.problems  # type: ignore[attr-defined]
    assert not [p for p in problems if "CSP violation" in p], problems  # connect-src 'self' covers ws://
    blocked = page.evaluate(
        """() => { try { new __pkWs.RealWebSocket('ws://127.0.0.1:9/x'); return 'opened'; }
          catch (e) { return e.name; } }"""
    )
    wait_until(page, "() => true")
    page.wait_for_timeout(200)
    assert blocked == "SecurityError" or any("CSP violation: connect-src" in p for p in problems)


def test_refused_token_signs_out(term: TermFactory) -> None:
    page = term(allow_errors=True)
    page.evaluate("__pkWs.expireToken()")
    connect(page, "web-prod")
    expect(page.get_by_role("heading", name="Sign in to Port-Key").first).to_be_visible()
    expect(panel(page).locator(".session__panel")).to_contain_text("Port-Key signed you out.")
