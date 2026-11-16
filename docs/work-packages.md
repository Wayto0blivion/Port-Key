# Port-Key work packages: file ownership

Package 0 (contracts and skeleton) is done and frozen. Packages 1, 2, 3, 7 and 8a (phase A) are
built and integrated; next is 4; then 5, 6 and 8b; then 9. Everyone works in the same tree on
branch `dev/v1`.

Before you start, read every file in `docs/handoff/`: `phase-a.md` collects what the phase A
engineers left for each later package, and each package writes `docs/handoff/<package>.md`
(what the next engineers call, the gotchas, what is left undone) when it finishes.

## The rule

**Edit only files you own.** If you need a change in someone else's file, or in a contract
(anything owned by package 0), do not edit it: write the change you need, and why, in your final
report. Contracts: `portkey/api/schemas/`, `portkey/interfaces.py`, `portkey/models.py`,
`portkey/errors.py`, `portkey/ssh/errors.py`, the schema in `portkey/store/db.py`,
`portkey/services.py`, `portkey/api/deps.py`, `docs/api.md`, `docs/ws-protocol.md`,
`portkey/static/js/types.js` and the function names in `portkey/static/js/api.js`.

Also:

- Keep the signatures in your stub files: other packages import them. Adding private helpers,
  new private modules inside your own directory, and extra public functions is fine.
- Stub constructors must stay free of I/O and must not raise (`services.build_services` calls
  them at app start). `MemorySessionRegistry.start/aclose` must stay safe to call.
- Put fixtures you need in a `conftest.py` inside your own test directory, never in
  `tests/conftest.py`.
- No file over about 400 lines; split into private modules in your own directory.
- Commit only your own files (`git add <your paths>`, never `git add -A`), on `dev/v1`, with
  `git -c user.name="John" -c user.email="98188354+Wayto0blivion@users.noreply.github.com" commit`
  and the trailer `Co-Authored-By: Claude <noreply@anthropic.com>`. Never push.

## Running checks

Everything runs in containers. This box has no `make`; `./mk` runs make in a container (it uses
the host's make when there is one). Always pass your own compose project name so parallel runs
do not collide:

```sh
./mk check P=portkey-wp3        # ruff check + ruff format --check + mypy + unit tests
./mk unit P=portkey-wp3         # unit tests only
./mk lint-js P=portkey-wp8a     # eslint + tsc --checkJs on portkey/static/js
./mk frontend P=portkey-wp8b    # tests/frontend: Chromium against a mock API, no network
./mk frontend P=portkey-wp8b FRONTEND=tests/frontend/terminal   # one directory or file
./mk fmt P=portkey-wp3 FILES="portkey/keys portkey/api/routes_keys.py tests/unit/keys"
                                # ruff format + ruff check --fix, on your paths only
./mk check P=portkey-wp3 FILES="portkey/keys tests/unit/keys" TESTS=tests/unit/keys
                                # lint/unit narrowed to your paths (mypy always runs on all)
./mk integration P=portkey-wp4  # sshd containers + tests/integration (package 7 completes)
./mk e2e P=portkey-wp9          # app + Playwright (package 7 harness, package 9 specs)
./mk clean P=portkey-wp4        # stop and remove that project's containers and volumes
```

To run a subset: `docker compose -f compose.dev.yaml -p portkey-wp3 run --rm --no-deps test
pytest tests/unit/keys -k textio` (export `HOST_UID=$(id -u) HOST_GID=$(id -g)` first, or the
container runs as uid 1000). Images are shared (`portkey-dev:local`, `portkey-lint-js:local`)
and the source is bind-mounted, so code changes need no rebuild; rebuild only after changing
`requirements*.lock` (`./mk build`). Never run `./mk fmt` without `FILES`: it would rewrite
other packages' files. Before you report done, run the full `./mk check P=<yours>` (whole tree)
and `./mk lint-js` plus `./mk frontend` if you touched JavaScript, CSS or `index.html`; a
failure in a file you do not own goes in your report, not into a fix.

From async code on a hot path (SSH connect, terminal sessions, the wizard), call the SQLite
repositories through `asyncio.to_thread`; see `portkey/interfaces.py` and
`docs/handoff/phase-a.md`.

## Ownership

| Path | Owner |
|---|---|
| `pyproject.toml`, `requirements.lock`, `requirements-dev.lock`, `scripts/lock.sh` | 0 |
| `Dockerfile`, `.dockerignore`, `.gitignore`, `compose.yaml`, `compose.dev.yaml` (services `test`, `lint-js`, `frontend`) | 0 |
| `compose.dev.yaml` services `integration`, `sshd`, `sshd-rekeyed`, `sshd-inner`, `app`, `evil-origin`, `e2e` | 7 (edit only those service blocks) |
| `Makefile`, `mk`, `tools/make/`, `tools/lint-js/`, `eslint.config.js`, `jsconfig.json` | 0 |
| `scripts/vendor-xterm.sh`, `portkey/static/vendor/xterm/` | 0 |
| `README.md`, `docs/api.md`, `docs/ws-protocol.md`, `docs/work-packages.md` | 0 |
| `docs/PLAN.md`, `docs/architecture.md`, `docs/ux-spec.md`, `docs/design/` | owner (via package 0) |
| `docs/windows-smoke.md` | 9 |
| `portkey/__init__.py`, `__main__.py`, `healthcheck.py`, `config.py`, `models.py`, `interfaces.py`, `errors.py`, `services.py` | 0 |
| `portkey/app.py` | 0, except the MIDDLEWARE section: 1 |
| `portkey/api/schemas/` (all), `portkey/api/deps.py`, `portkey/api/__init__.py` | 0 |
| `portkey/security/` (all), `portkey/api/routes_auth.py` | 1 |
| `portkey/store/db.py` | 0 (schema); 2 may append migrations only after a contract change is agreed |
| `portkey/store/` everything else: `connections.py`, `groups.py`, `tags.py`, `hostkeys.py`, `settings.py`, `events.py`, `keys_meta.py` | 2 |
| `portkey/api/routes_connections.py`, `routes_groups.py`, `routes_settings.py` | 2 |
| `portkey/keys/` (all), `portkey/api/routes_keys.py`, `routes_hosts.py` | 3 |
| `portkey/ssh/errors.py` | 0 |
| `portkey/ssh/client.py`, `hostkey.py`, `chain.py`, `portkey/api/ws_common.py` | 4 |
| `portkey/sessions/` (all), `portkey/api/ws_terminal.py`, `routes_sessions.py` | 5 |
| `portkey/wizard/` (all), `portkey/api/ws_install.py` | 6 |
| `portkey/api/routes_diag.py` | 7 |
| `portkey/static/index.html`, `css/base.css`, `js/types.js`, `js/globals.d.ts`, `js/ui/fpstrip.js` | 0 |
| `portkey/static/js/api.js` | 8a (function names frozen by 0) |
| `js/main.js`, `js/store.js`, `js/shell.js` | 8a |
| `js/views/login.js`, `connections.js` (sidebar folder tree, tag chips, filter), `home.js` (table, bulk bar, first run), `edit-connection.js`, `keys.js`, `suggestions.js`, `settings.js`, `diagnostics.js` | 8a |
| `js/ui/dom.js`, `toast.js`, `modal.js`, `tabstrip.js`, `folderpicker.js`, `tagfield.js` | 8a |
| `js/parts/` (private modules of 8a's views) | 8a |
| `css/layout.css`, `css/dialogs.css`, `css/parts/` (pulled in by `layout.css`) | 8a |
| `js/views/wizard.js` (including its folder and tag fields), `views/sessions.js` | 8b |
| `js/terminal/session.js`, `terminal/tabs.js`, `js/ws/protocol.js`, `js/ui/dialogs.js`, `ui/palette.js` | 8b |
| `js/terminal/` (any further module), `js/wizard/` (private modules of `views/wizard.js`, `views/sessions.js`, `ui/dialogs.js`, `ui/palette.js`) | 8b |
| `css/terminal.css`, `css/prompts.css`, `css/wizard.css`, `css/terminal/` (private parts they `@import`) | 8b |
| `tests/conftest.py`, `tests/unit/skeleton/` | 0 |
| `tests/unit/security/` (incl. routes_auth tests) | 1 |
| `tests/unit/store/` (repositories, folders and tags, routes_connections/groups/settings) | 2 |
| `tests/unit/keys/` (incl. routes_keys/hosts) | 3 |
| `tests/unit/ssh/` (incl. ws_common) | 4 |
| `tests/unit/sessions/` (incl. ws_terminal, routes_sessions) | 5 |
| `tests/unit/wizard/` (incl. the remote script under dash, bash, busybox sh; ws_install) | 6 |
| `tests/unit/diag/` | 7 |
| `tests/sshd/`, `tests/evil-origin/`, `tests/fixtures/`, `tests/e2e/Dockerfile`, `tests/e2e/conftest.py` and harness helpers, `tests/integration/conftest.py` | 7 |
| `tests/integration/ssh/` | 4 |
| `tests/integration/sessions/` | 5 |
| `tests/integration/wizard/` | 6 |
| `tests/e2e/test_*.py` (the Playwright specs) | 9 (`test_shell_seams.py` was written at the phase A integration; 9 owns it now) |
| `tests/frontend/` (conftest, `mock/`, `serve.py`, `run.sh`, `screenshots.py`, `test_*.py`) | 8a |
| `tests/frontend/terminal/` (8b's browser tests; they reuse `tests/frontend/conftest.py`, and add WebSocket mocks of their own here) | 8b |
| `docs/handoff/<package>.md` | that package |

## What each package delivers (summary)

| # | Package | Implements |
|---|---|---|
| 1 | Security | Host/Origin/X-Port-Key/Content-Type guard, scrypt app password with backoff, tokens, CSP and headers, log redaction, the 422 handler, the middleware section of `app.py`, `/api/auth/*` |
| 2 | Storage | Every repository in `store/` including folders (depth 3, sibling names, move-under-descendant, delete re-parenting) and tags (normalisation, rename-merge, last-use cleanup), bulk edit (atomic), connection and settings routes, folder and tag routes |
| 3 | Keys | `textio`, discovery with dedupe and display paths, keygen (O_EXCL + rename), vault with TTL, known_hosts parsing and suggestions, key and host routes |
| 4 | SSH core | asyncssh client callbacks, host-key decision table and prompts, jump chains, `WsPrompter` and the WebSocket auth handshake |
| 5 | Terminal | Ring buffer, sessions with detach/reattach, registry and reaper, `/ws/terminal`, session routes |
| 6 | Wizard | Remote script, install flow with `needs_action`, `/ws/install` |
| 7 | Test infrastructure | sshd images and users, fixtures (TEST-ONLY keys, CRLF/UTF-16 `.ssh`), evil-origin page, Playwright harness, `routes_diag.py` |
| 8a | Frontend shell | `api.js`, store, shell, sidebar folder tree and tag chips, Home with bulk bar, Keys, suggestions, Settings, Diagnostics, login, edit connection, shared UI widgets |
| 8b | Frontend terminal | Terminal tabs and sessions, WebSocket protocol client, connection-time dialogs, palette, wizard |
| 9 | End-to-end | Playwright specs and `docs/windows-smoke.md` |

Tests each package must list are in `docs/architecture.md` section 10.
