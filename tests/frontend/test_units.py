"""Pure frontend functions and the HTTP client contract (package 8a).

The modules are imported inside Chromium (they are browser ES modules); the mock API records
every request so the client's headers and bodies can be checked against docs/api.md.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from tests.frontend.conftest import AppFactory, calls


def run(page: Page, module: str, body: str) -> Any:
    """Evaluate `body` (an expression using `m`, the imported module) in the page."""
    return page.evaluate(f"async () => {{ const m = await import('/js/{module}'); return {body}; }}")


CONNS = """[
  {id: 'a', name: 'web-prod', host: '192.168.10.42', port: 22, username: 'john', notes: 'Production',
   group_id: 'g2', tags: ['linux', 'prod'], jump_id: null},
  {id: 'b', name: 'imaging-01', host: '192.168.10.31', port: 2222, username: 'admin', notes: '',
   group_id: 'g3', tags: ['appliance'], jump_id: null},
  {id: 'c', name: 'vpn-gw', host: '10.8.0.1', port: 22, username: 'john', notes: 'Needs VPN up',
   group_id: null, tags: ['linux'], jump_id: null},
]"""
GROUPS = """[
  {id: 'g1', name: 'Office LAN', parent_id: null, sort_order: 0, connection_count: 1},
  {id: 'g2', name: 'Servers', parent_id: 'g1', sort_order: 0, connection_count: 1},
  {id: 'g3', name: 'Appliances', parent_id: null, sort_order: 0, connection_count: 1},
  {id: 'g4', name: 'Linux', parent_id: 'g2', sort_order: 0, connection_count: 0},
]"""


def test_parse_filter(app: AppFactory) -> None:
    page = app()
    out = run(page, "views/connections.js", "m.parseFilter('  Web  #Prod #prod ##web  srv ')")
    assert out == {"terms": ["web", "srv"], "tags": ["prod", "web"]}
    assert run(page, "views/connections.js", "m.parseFilter('#')") == {"terms": [], "tags": []}


def test_filter_connections(app: AppFactory) -> None:
    page = app()

    def ids(text: str, tags: str = "[]") -> list[str]:
        result: list[str] = run(
            page, "views/connections.js",
            f"[...m.filterConnections({CONNS}, {GROUPS}, {text!r}, {tags})].sort()",
        )
        return result

    assert ids("") == ["a", "b", "c"]
    assert ids("office") == ["a"]  # folder path "Office LAN / Servers"
    assert ids("servers web") == ["a"]  # every term must match
    assert ids("admin@192.168.10.31:2222") == ["b"]
    assert ids("vpn up") == ["c"]  # notes
    assert ids("#linux") == ["a", "c"]
    assert ids("#linux", "['prod']") == ["a"]  # text tags AND chip tags
    assert ids("", "['linux', 'appliance']") == []
    assert ids("#nope") == []


def test_normalize_tag(app: AppFactory) -> None:
    page = app()
    cases = {
        "#Prod": "prod", " web ": "web", "##dell": "dell", "v1.2_x-y": "v1.2_x-y",
        "Bad Tag": None, "-lead": None, "a" * 33: None, "": None, "ok!": None, "a" * 32: "a" * 32,
    }
    for raw, expected in cases.items():
        assert run(page, "ui/tagfield.js", f"m.normalizeTag({raw!r})") == expected, raw


def test_address_and_ssh_command(app: AppFactory) -> None:
    page = app()
    base = "{username: 'admin', host: '192.168.10.31', port: 2222, jump_id: null}"
    assert run(page, "ui/dom.js", f"m.formatAddress({base})") == "admin@192.168.10.31:2222"
    assert run(page, "ui/dom.js", "m.formatAddress({username: 'john', host: '10.8.0.1', port: 22})") == "john@10.8.0.1"
    assert run(page, "ui/dom.js", "m.formatAddress({username: 'u', host: 'fe80::1', port: 2200})") == "u@[fe80::1]:2200"
    assert run(page, "ui/dom.js", f"m.sshCommand({base})") == "ssh -p 2222 admin@192.168.10.31"
    chain = """m.sshCommand({id: 't', username: 'root', host: '192.168.10.27', port: 22, jump_id: 'j'},
      [{id: 'j', username: 'john', host: '192.168.10.20', port: 22, jump_id: 'v'},
       {id: 'v', username: 'john', host: '10.8.0.1', port: 2222, jump_id: null}])"""
    assert run(page, "ui/dom.js", chain) == "ssh -J john@10.8.0.1:2222,john@192.168.10.20 root@192.168.10.27"


def test_format_when(app: AppFactory) -> None:
    page = app()
    now = "new Date(2026, 10, 15, 16, 0)"
    cases = {
        "new Date(2026, 10, 15, 14, 2)": ("Today 14:02", "Last connected 14:02 today"),
        "new Date(2026, 10, 14, 9, 0)": ("Yesterday", "Last connected yesterday"),
        "new Date(2026, 10, 9, 9, 0)": ("9 Nov", "Last connected 9 Nov"),
        "new Date(2025, 2, 2, 9, 0)": ("2 Mar 2025", "Last connected 2 Mar 2025"),
    }
    for when, (short, long) in cases.items():
        assert run(page, "ui/dom.js", f"m.formatWhen({when}.toISOString(), {now})") == short
        assert run(page, "ui/dom.js", f"m.formatWhenLong({when}.toISOString(), {now})") == long
    assert run(page, "ui/dom.js", "m.formatWhen(null)") == "Never"


def test_folder_index(app: AppFactory) -> None:
    page = app()
    expr = f"""(() => {{ const i = m.buildFolderIndex({GROUPS});
      return {{ path: i.path('g4'), depth: [i.depth('g1'), i.depth('g4'), i.depth(null)],
        desc: [...i.descendants('g1')].sort(), height: [i.height('g1'), i.height('g3')],
        order: i.ordered.map((o) => o.path) }}; }})()"""
    out = run(page, "parts/folders.js", expr)
    assert out["path"] == "Office LAN / Servers / Linux"
    assert out["depth"] == [1, 3, 0]
    assert out["desc"] == ["g2", "g4"]
    assert out["height"] == [3, 1]
    assert out["order"] == ["Appliances", "Office LAN", "Office LAN / Servers", "Office LAN / Servers / Linux"]


def test_requests_follow_the_contract(app: AppFactory) -> None:
    page = app()
    run(page, "api.js", "m.updateSettings({font_size: 15})")
    run(page, "api.js", "m.deleteTag('legacy')")
    get = next(c for c in calls(page) if c["method"] == "GET" and c["path"] == "/api/connections")
    assert get["auth"] is True
    assert get["csrf"] is None
    assert get["json"] is None
    for call in calls(page):
        if call["method"] != "GET":
            assert call["csrf"] == "1", call
            assert call["json"] == "application/json", call
            assert call["body"] is not None, call  # always a JSON body, {} when empty
    delete = next(c for c in calls(page) if c["method"] == "DELETE")
    assert delete["path"] == "/api/tags/legacy"
    assert delete["body"] == {}


def test_api_errors_and_sign_out(app: AppFactory) -> None:
    page = app()
    page.evaluate("window.__pkMock.failNext('/api/groups', 409, 'name_taken', 'A folder named VPN already exists there.')")
    err = run(page, "api.js", "m.createGroup('VPN').then(() => null, (e) => ({name: e.name, status: e.status, code: e.code, message: e.message}))")
    assert err == {"name": "ApiError", "status": 409, "code": "name_taken", "message": "A folder named VPN already exists there."}
    # A wrong password on login is not a sign-out; an expired token is.
    bad = run(page, "api.js", "m.login('nope').then(() => null, (e) => [e.code, m.getToken()])")
    assert bad == ["bad_password", "mock-token"]
    page.evaluate("window.__pkMock.expire()")
    gone = run(page, "api.js", "m.listTags().then(() => null, (e) => [e.code, m.getToken()])")
    assert gone == ["unauthorized", None]
    page.wait_for_selector("dialog.login[open]")
