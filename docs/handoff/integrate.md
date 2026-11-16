# Handoff: phase A integration

Package key `integrate`, 2026-11-16, on `dev/v1` after packages 1, 2, 3, 7 and 8a. The notes the
phase A engineers left for later packages are in `docs/handoff/phase-a.md`; read that first.
This file covers what the integration changed, what you call now, and what is still open.

## State of the tree

All green on the whole tree, every suite in containers (compose project `portkey-int`):

| Command | Result |
|---|---|
| `./mk check` | ruff and format clean (139 files), mypy clean (81 files), 677 unit tests pass |
| `./mk lint-js` | eslint and `tsc --checkJs` clean |
| `./mk frontend` | 58 browser tests pass (Chromium against the mock API, no network) |
| `./mk integration` | 31 pass |
| `./mk e2e` | 14 pass (7 harness self-tests, 7 in `test_shell_seams.py`); also 6 reruns in a row against one running stack |
| `./mk clean` | no containers or networks left |

## Commands and tooling you use now

- `./mk frontend P=<project> [FRONTEND=tests/frontend/terminal]` runs the frontend browser
  tests through the new compose service `frontend` (profile `frontend`, `network_mode: none`).
  `tests/frontend/run.sh test|shots` still works for screenshots.
- There is one Playwright image, `portkey-e2e:local`, built from `tests/e2e/Dockerfile`, for
  both e2e and the frontend suite. The private `tests/frontend/Dockerfile` (`portkey-ui8a:local`)
  is gone; that local image can be deleted with `docker image rm portkey-ui8a:local`.
- `tests/e2e/Dockerfile` does build: a `--no-cache` build passed. Package 8a's "does not build"
  was true of package 0's first version (no `python3-venv` in the base image); package 7 had
  already added it. Both reports were right at the time they were written.
- The dev image has dash, bash and busybox; `tests/unit/skeleton/test_dev_image.py` runs each.
- `docs/work-packages.md` now lists `tests/frontend/`, `js/parts/` and `css/parts/` under 8a,
  gives 8b `tests/frontend/terminal/`, `js/terminal/`, `js/wizard/` and `css/terminal/`, gives
  9 `tests/e2e/test_shell_seams.py`, and asks every package to write `docs/handoff/<key>.md`.

## Contract changes (docs/api.md decisions 13 to 16)

- **Malformed tags** in a request body are 422 `validation` (the schema checks the pattern);
  `tag_invalid` remains for the repository's own checks (a bulk `add_tags` over 32 tags,
  direct callers such as the wizard).
- **Suggestions** no longer carry `saved` (schema, route, `types.js`, views, mock). The server
  leaves out saved and dismissed hosts.
- **Tokens** die 90 days after issue however often they are used (`Limits.token_max_age`;
  `SqliteTokenStore(db, ttl, clock, *, max_age=...)`). Renewal never moves `expires_at` past
  `created_at + 90 days`, and older rows are capped by `created_at` too. The table already had
  `created_at`, so there is no migration.
- **Hide with reassign** (`PATCH /api/keys/{id}`) is one transaction, label change included:
  the route wraps the calls in `services.db.tx()`. A test makes the last write fail and checks
  nothing changed.
- docs/api.md also states: reachability omits `ms` unless `up`; a hidden key counts as
  `key_unknown` for `key_fp` and `default_key_fp`; rename or delete of an unused tag is 404
  `not_found`; password change can answer 429 `rate_limited`.
- `portkey/interfaces.py`: `AppAuth.change_password` may raise `RateLimited`; the header now
  says async hot paths call repositories through `asyncio.to_thread`.

## What running the shell against the real backend found (all fixed)

`tests/e2e/test_shell_seams.py` drives sign-in, the sidebar, Home, edit and bulk tagging, Keys,
Settings, Diagnostics, the 501 routes and the 401 path against the `app` container, and fails
on any page error, console error or unexpected `/api` response of 400 or more.

1. **Row menus vanished at once in a 1280 px window.** The Home table is wider than its pane
   below about 1440 px, so a "⋯" button sits half hidden; revealing it scrolls the table after
   the menu opened, and menus closed on any scroll. A menu on an anchor now follows the anchor
   while it is in view (`parts/menu.js`); a frontend test reproduces it at 1280 px.
2. **Settings redrew completely when the key list arrived late**, wiping a half-typed password
   and the "Saved" note. The page is now built once per settings load; key changes only
   refresh the Default key options (`views/settings.js`), with a frontend test.
3. **`sign_in_with_token` raced the signed-out boot** (which clears the stored token when
   `/api/auth/status` answers). The e2e helper now waits for the boot first.
4. **The mock's key name check** treated every key name as taken; the real API only looks in
   the generated-keys folder, ignoring case. The mock and its test now match.
5. A rate-limited password change shows its message on the field instead of a toast.

No seam problems with auth headers, error envelopes or field names turned up: the views read
the real responses as they read the mock's.

## Gotchas

- **Console noise in e2e.** On `http://app:8000` Chromium logs "The Cross-Origin-Opener-Policy
  header has been ignored..." as a console error, because that origin is not trustworthy.
  `http://localhost:8822` on the PC is, so it does not happen there. `test_shell_seams.Watch`
  filters exactly that line; specs that fail on console errors need the same filter.
- **Backoff in e2e is shared.** Every request reaches the app from one peer address, and wrong
  current passwords on a password change share one bucket for the life of the app process: after
  3 wrong ones in a run, the next answer is 429. Keep wrong-password specs few, or accept 429.
- **The e2e stack keeps state** while it is up (tmpfs data): rerunning specs against a running
  stack sees earlier connections, keys and settings. `test_shell_seams.py` is written to be
  rerun; follow that pattern (create by name if missing, change a value to something different
  from its current one).
- **api.js 401 rule:** a 401 clears the token on every call except login and password change.
  On password change, `bad_password` (a wrong current password) is not a sign-out, but
  `unauthorized` (the token itself expired) is, because the owner really is signed out then.
  Fetch must keep the default mode; a test spies on `fetch` to keep it so.
- The frontend's 8b stubs throw "not implemented (package 8b)"; `parts/actions.js` turns that
  into a "not built yet" toast. Replacing the stubs needs no change in 8a's files.

## Left undone

- The first-visit setup code (package 1's request) waits on the owner; see
  `docs/handoff/phase-a.md`, "Open decisions".
- The Home table needs horizontal scrolling below about 1440 px wide (it has `min-width:
  1040px` beside a 280 px sidebar). It works, but a laptop at 150 % scaling (1280 px) scrolls
  sideways to reach the row menu. Not changed here; worth a look by whoever next touches 8a's
  CSS, against `docs/design/Main.dc.html`.
- Unchanged open items from the phase A reports (browser proof of `connect-src` for WebSockets
  and xterm under the CSP, Home table rebuild cost, Windows font, folder drag) are listed with
  their status at the end of `docs/handoff/phase-a.md`.
