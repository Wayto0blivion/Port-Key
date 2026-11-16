# Handoff: package 9, end to end

Package key `wp9-e2e`, 2026-11-16, on `dev/v1`. Read the other files in `docs/handoff/` first.
This file covers what the end-to-end package built and changed, what it found, how the open
items from earlier packages ended, and what is left for the owner's Windows PC.

## State

| Command | Result |
|---|---|
| `./mk check P=portkey-wp9` | ruff and format clean (218 files), mypy clean (92 files), 1052 unit tests pass, none skipped |
| `./mk lint-js P=portkey-wp9` | eslint and `tsc --checkJs` clean |
| `./mk frontend P=portkey-wp9` | 137 pass |
| `./mk integration P=portkey-wp9i` | 90 pass |
| `./mk e2e P=portkey-wp9` | 40 pass (7 harness self-tests, 33 specs); three fresh runs in a row green, and two runs against one running stack (the second skips the first-run spec by design) |
| runtime image, `docker run --read-only --cap-drop ALL --security-opt no-new-privileges`, no port published | healthy; uid 1000, every capability set empty; `/app`, `/etc` and the `.ssh` mount read-only, `/data` and `/keys-generated` writable; setup, diagnostics (`.ssh` not writable, as on the PC), key generation (file 0600) and `reset_password` all worked |
| `./mk clean` for `portkey-wp9` and `portkey-wp9i` | run at the end; nothing left |

## The e2e set-up now matches the PC

The e2e `app` service runs with the PC's own Host/Origin allowlist (`localhost:8822`,
`127.0.0.1:8822`, `[::1]:8822`) and listens on 8822. Chromium is launched with
`--host-resolver-rules=MAP localhost app` (`tests/e2e/conftest.py` `browser_args`), so the
browser opens `http://localhost:8822` exactly as the owner's does: a secure context (the async
clipboard works, Cross-Origin-Opener-Policy is honoured, so the old console-noise filter is
gone), the PC's Host and Origin, and a same-origin `ws://localhost:8822` under
`connect-src 'self'`. `AppApi(base_url)` sends the PC's Host and Origin to
`$PORTKEY_CONNECT_TO` (`app:8822`). The evil-origin page now attacks `ws://localhost:8822`.
`tests/frontend/terminal/smoke.py` uses the same mapping.

## Specs (`tests/e2e/`)

| File | Covers |
|---|---|
| `test_00_first_run.py` | Set a password (client-side checks first), first-run card with the Windows path, `already_setup`, sign in again. Skips on a stack that already has a password |
| `test_01_suggestion.py` | Add from the `sshd:2222` known_hosts suggestion; wizard finds the key already installed; the suggestion goes |
| `test_terminal.py` | Type and read `.xterm-rows`; 24-bit colour under the CSP; two sessions to one host; reload, reattach and replay into the same shell |
| `test_hostkeys.py` | First-seen dialog against `sshd-rekeyed` (Cancel, then Trust); jump-host first-seen; changed key declined, then replaced by typing the name |
| `test_jump.py` | A terminal on `sshd-inner` through `sshd`; the gateway cannot be deleted |
| `test_keys.py` | Copy public key and install command (clipboard read back); passphrase key: wrong then right passphrase, held, Lock from the top bar; generate a key and install it with the wizard |
| `test_wizard.py` | Password user with a wrong password first; the 2FA user; password login disabled with the manual command and Check key login |
| `test_folders_tags.py` | New folder and nested folder from the sidebar menus, drag a connection in, bulk tag three, filter by chip and `#tag` |
| `test_security.py` | Evil-origin page; 403 handshake from another origin; 4401 for a bad token; 421 for wrong Host headers (API, static, in the browser); Origin and `X-Port-Key` checks; CSP header |
| `test_shell_seams.py` | The phase A seams spec; its 501 test is now a session-route check |

Every spec watches page errors, console errors and CSP violations (`helpers.CSP_WATCH`
reports each violation as a console error) and every `/api` answer of 400 or more
(`helpers.Watch.check(*allowed)`). Specs are rerunnable against a running stack: they create
by name, reset what they change (sshd users through `reset_ssh_user`, host key records through
`Api.forget_host_keys`) and kill earlier sessions first.

Run one file against a running stack:

```sh
export HOST_UID=$(id -u) HOST_GID=$(id -g)
docker compose -f compose.dev.yaml -p portkey-wp9 --profile e2e up -d --wait app evil-origin
docker compose -f compose.dev.yaml -p portkey-wp9 --profile e2e run --rm --no-deps e2e \
    pytest -p no:cacheprovider -m e2e tests/e2e/test_terminal.py
```

*Later (setup code):* on a fresh stack the specs need the app's log for the first-visit setup
code: put `PORTKEY_APP_LOG="$(docker compose ... logs --no-color app)"` in front of the `run`
(`tests/e2e/harness/app_log.py`; `./mk e2e` does it for you).

`tests/e2e/screens.py` takes screenshots of the real app (command in its docstring).

## Bugs found and fixed

1. **Wizard opened before the key list arrived** said "Port-Key has no key to install" and chose
   Password only. **Edit connection** had the same race, and saving would have switched a
   connection's own key to the default key. Both wait for the store's first load now
   (`views/wizard.js`, `views/edit-connection.js`), with frontend tests.
2. **Keys table squeezed into its own scroll box** on a short window, clipping the last row
   (`.page > * { flex-shrink: 0 }`), with a frontend test.
3. **Home table at 1280 px** (integrate's open item): no more fixed `min-width: 1040px`; in a
   pane under about 1000 px the Notes column gives way (container query), so row menus stay in
   view without sideways scrolling. Frontend test at 1280; the menu-follows-anchor test moved to
   1100 px, where the table still scrolls.
4. **Tab names for screen readers** read "connectingweb-prod"; the state now follows the title.
5. **Folder counts** said "1 connections" to screen readers.
6. **Right-click menus closed whenever a terminal printed**: a menu at the pointer closed on
   any scroll, including a busy terminal's own viewport. It now closes only when the page or
   the list holding its row scrolls (`parts/menu.js`), with a frontend test.
7. **Activity dot** replaced the status dot (8b's request): it is a separate dot now.
8. **A failed restore after reload** was silent (8b's request): it shows a toast (no test: the
   mock cannot make the restore fail).

## Open items from earlier packages

| From | Item | Outcome |
|---|---|---|
| integrate, 1 | One-time setup code on `POST /api/auth/setup` | **Not done: waits on the owner** (it changes the contract and the first-run screen). Still listed under "Open decisions" in `phase-a.md`. `reset_password` (below) reopens the same window, which the README says. *Later: approved and built, reset path included; `docs/handoff/setup-code.md`* |
| integrate | Home table needs sideways scrolling at 1280 px | Fixed (bug 3) |
| 1, integrate | `connect-src 'self'` covering `ws://localhost:8822`; xterm under the CSP | Proven in a real Chromium at `http://localhost:8822` (`test_terminal.py`, `test_security.py`) |
| 8a | Home table rebuilds every row on each change | Not changed; not a problem at the test bed's size |
| 8a | Windows fonts, folder drag | Fonts: `docs/windows-smoke.md` step 8. Folder drag: not required, not done |
| integrate | `portkey-ui8a:local` image unused | Left in place (deleting it is the owner's call): `docker image rm portkey-ui8a:local` |
| integrate | api.js signs out on `unauthorized` during a password change, not on `bad_password` | Kept as is: the owner really is signed out then |
| 4 | `FakePrompter` keywords; drop `ssh/_compat.py` | Done; the wizard's own signature checks went too |
| 5 | `test_shell_seams.py` expected 501 | Done |
| 5, 8b | `docs/ws-protocol.md`: replay offset, attach once after a detached exit, any frame answers a ping | Done (decisions 18, 19) |
| 5 | Remove the always-skipped stub test | Replaced by a services wiring test |
| 6 | `InstallWizard(..., groups=groups)` | Done in `services.py`, with a test |
| 6 | `/ws/install` details in the protocol | Done ("Step details", decision 20) |
| 6 | `types.js` error codes | Nothing to add: `WsError.code` is a plain string there |
| 6 | Host key verdict source from the SSH core | Done: `status hostkey` carries `source`; the wizard maps it to `known`/`trusted` (decision 21) |
| 6 | `copy.js` texts for `perms_warning`, install and verify failures, input-rejected codes | Already done by 8b (`run.js` keeps the earlier `install` text; `INPUT_CODES` go back to the form); no change |
| 8b | `serve.py` sends the app's CSP | Done; the terminal suite's private server is gone |
| 8b | Separate activity dot; toast on failed restore | Done (bugs 6, 7) |

## New for the owner

- `python -m portkey.reset_password --yes` (in the container: `docker compose exec portkey
  ...`) clears the app password and every token, keeping everything else. README "Forgot the
  app password".
- `docs/windows-smoke.md`: the checklist for the PC, and what to send back.

## Gotchas

- A second click on the same sidebar row within 500 ms counts as a double click (one action).
  Specs that cancel and reopen wait 600 ms.
- A sidebar row's "⋯" shows only on hover or focus; specs use right-click (`helpers.row_menu`).
- Playwright's sync API keeps an event loop running, so asyncio code (resetting sshd users) runs
  in a worker thread (`helpers.reset_ssh_user`).
- The changed-key spec uses the name `localhost` through two different jump hosts: through
  `sshd` it is sshd's own port 22 (set A), through `sshd-rekeyed` that server (set B).
- `./mk e2e` builds the app image from the tree; when running specs against a stack you keep up,
  rebuild `app` after changing the frontend.
- The TOTP module on the test bed allows code reuse, so a reset right after a 2FA wizard run
  works in the same 30 s window.

## Left undone or unverified

- Everything in `docs/windows-smoke.md`: Docker Desktop mounts and ACLs, VPN/Tailscale and
  hosts-file names, Windows ssh with a generated key, Edge's Ctrl+Shift+K, Cascadia Mono.
- From earlier packages, still without a test bed: `@cert-authority` host certificates,
  the fallback fetch when no trusted key type is offered, `password_expired` against sshd, a
  read-only `authorized_keys` against sshd, `exit lost` by cutting a live network, a real
  15-minute reaper run, reattach after an app container restart.
- The first-visit setup code (owner decision). *Later: built, `docs/handoff/setup-code.md`.*
- `tests/unit/ssh/fakes.py` `KwPrompter` is now redundant with `FakePrompter`; left in place.
- Process slips (no project code ran on the host in any of them): once an empty host
  `python3 -` invocation by mistake; two assertion edits in `tests/e2e/test_terminal.py` with
  host `sed -i`; two regression tests appended to `tests/frontend/test_dialogs.py` and
  `tests/frontend/terminal/test_wizard.py` with a host shell heredoc; one `sed -i` aimed at
  `/dev/null` that failed and changed nothing. Every other edit used the file tools and every
  run was in a container.
