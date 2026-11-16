# Handoff: review fixes

Package key `review-fixes`, 2026-11-16, on `dev/v1`. Three reviews (security, backend,
frontend and release) produced 30 findings. Each was reproduced or proven from the code before
it was changed; every fix has a regression test at the lowest level that catches it, checked to
fail on the old code where that was practical.

## State

| Command | Result |
|---|---|
| `./mk check P=portkey-fix` | ruff and format clean (231 files), mypy clean (94 files), 1149 unit tests pass |
| `./mk lint-js P=portkey-fix` | eslint and `tsc --checkJs` clean |
| `./mk frontend P=portkey-fix` | 155 pass |
| `./mk integration P=portkey-fixi` | 90 pass |
| `./mk e2e P=portkey-fixe` | 41 pass |
| runtime image, `--read-only --cap-drop ALL --security-opt no-new-privileges --memory 1g --pids-limit 256`, no port published | healthy; uid 1000, every capability set empty, root filesystem read-only, `/data` writable and migrated to schema 2; a 1 GB `Content-Length` answered 413 at once |

## What changed (contracts first)

- **Schema version 2** (`store/db.py`): `host_keys` gains `via` (the jump connection a host is
  reached through, `''` when direct) and `distrusted`. Existing records become direct ones.
  `HostKeyStore.for_host/add/replace` take `via`; `replace` takes `supersede` (known_hosts keys
  to distrust). `HopRef.via` carries the route; `ChainConnector.plan/plan_new` set it.
- **HTTP**: 413 `payload_too_large` (bodies over 256 KB, `security/bodylimit.py`, inside the
  host guard); `POST /api/connections/{id}/restore` (Undo of a delete, same id);
  `GET /api/hostkeys` adds `via_id`, `via_name`, `distrusted`; unlock answers 422
  `key_unreadable` instead of 500. `docs/api.md` decisions 18 to 20.
- **WebSockets**: open sockets re-check their token every 10 s (`TokenStore.is_valid`, which
  never extends it) and close 4401 once it is revoked or expired; the wizard saves the key it
  installed instead of `key_fp: null`, and refuses a hidden key (4400 `key_unknown`).
  `docs/ws-protocol.md` decisions 8 and 22 and "Auth handshake".
- **Runtime**: `compose.yaml` sets `mem_limit: 1g` and `pids_limit: 256`; uvicorn gets
  `timeout_graceful_shutdown` 5 s; terminal input goes through a per-socket writer task so the
  receive loop keeps reading while a remote program takes no input; the wizard reads remote
  output through `create_process` with a 64 K cap per stream.
- **Frontend**: Edit connection keeps and switches specific keys (the `key:SHA256:...` split);
  a tab "open in another tab" never kills that shell, and a duplicated browser tab asks the
  other pages before taking a session (`terminal/claims.js`, a BroadcastChannel); the wizard's
  first-seen step focuses its heading; OSC 8 links need Ctrl/Cmd+click and Port-Key's dialog
  (`terminal/links.js`); the verification dialog frames server text as the server's; the paste
  guard asks for one line ending in a line break when bracketed paste is off; the Disconnected
  bar tells a restart from "page was away".
- **Docs**: README backup and restore (tested against a scratch volume), the real volume name
  `portkey_portkey-data`, `.env` for `PORTKEY_SSH_DIR`; PLAN.md no longer names another tool's
  vulnerability or links the private canvas.

## For whoever works on it next

- The e2e changed-key spec (`tests/e2e/test_hostkeys.py`) now gets a changed key by pointing a
  jump connection at the other sshd: trust is per route, so `localhost` through two different
  jump hosts is two hosts (first seen on each), not a changed key.
- `tests/unit/sessions/fakes.py` `FakeChannel.write` raises `BrokenPipeError` once the remote
  has closed, as asyncssh 2.24 does; `tests/unit/wizard/fakes.py` `FakeConn` now fakes
  `create_process`, not `run`.
- `tests/unit/skeleton/test_main.py` checks uvicorn options against `uvicorn.Config`'s
  signature: building a `uvicorn.Config` would apply its `log_config` to the whole test
  process and break later log-capture tests.
- Deleted connections for Undo live in the repository's memory (10 minutes, at most 50); a
  restart ends the Undo.

## Left for the owner

- **Licence.** There is none; `pyproject.toml` says `Proprietary`. Choose one before the
  repository goes public (LICENSE, `pyproject.toml`, a README line).
- **Git history.** Not rewritten: earlier commits still contain the another tool statement and
  the private canvas link removed from PLAN.md, and 67+ commits carry a co-author trailer.
- **Real names.** `C:\Users\john`, `john@WORKPC` and the user `john` appear in docs,
  mockups, mock data and tests (about 55 files). Replacing them is mechanical but broad; the
  first name is in every commit's author field anyway.
- **Internal process material** in `docs/` (work packages, handoff notes, the smoke checklist
  addressed to the owner, design exports): keep, move to a private place, or exclude.
- **Shared login backoff.** A local process can keep the owner out of sign-in and setup for as
  long as it keeps failing (all clients share one address). Not changed: every shared throttle
  can be held, and a shorter one makes guessing faster. Suggested fix: an unlock code printed
  to the container log that lets a sign-in through a lockout. `docs/architecture.md` section 4.

## Process

One slip: an empty host heredoc into `/dev/null` (no file changed, no code ran). Every edit
used the file tools; every build, test and run was in a container.
