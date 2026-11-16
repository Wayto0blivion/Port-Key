# Handoff: package 8b, frontend terminal

Package key `wp8b-frontend-terminal`, 2026-11-16, on `dev/v1`. Read `phase-a.md`,
`integrate.md`, `wp4-ssh-core.md`, `wp5-terminal.md` and `wp6-wizard.md` first. This file covers
what the terminal tabs, the WebSocket client, the connection-time dialogs, the palette and the
install wizard give package 9 (and 8a), the decisions taken, and what is open.

## State

| Command (compose project `portkey-wp8b`) | Result |
|---|---|
| `./mk lint-js` | eslint and `tsc --checkJs` clean |
| `./mk frontend` | 132 pass (74 in `tests/frontend/terminal`, the shell's 58 unchanged); the 74 were green 5 runs in a row |
| `./mk check` (whole tree) | ruff, format, mypy clean; 1042 unit tests pass, 1 skip |
| `tests/frontend/terminal/smoke.py` against the e2e stack | 9 of 9 steps on a clean stack (below) |

No contract, shell or backend file was touched.

## Module map (all under `portkey/static/`)

| File | What |
|---|---|
| `js/ws/protocol.js` | `PortKeySocket` for both sockets: auth first, JSON and binary frames, prompts, ping, `closeState()` |
| `js/terminal/session.js` | `TerminalView`: one xterm per tab, byte count, states, resize, font |
| `js/terminal/lifecycle.js` | what a closed socket means: failure panels, Disconnected bar, reattach with backoff |
| `js/terminal/chrome.js`, `input.js`, `tools.js`, `xterm.js`, `messages.js`, `tabs.js` | session bar and frame DOM; keys, copy, paste; Find and Copy ssh command; the xterm factory; copy; tab titles and dots |
| `js/views/sessions.js` | open / focus / restore / close-all, `store.setSessionInfo`, font size, `(deleted)` |
| `js/ui/dialogs.js` | host key first seen and changed, passphrase, password, keyboard-interactive, paste and close-all questions |
| `js/ui/palette.js` | quick-connect palette, `rankConnections`, `parseTarget`, `parseQuery` |
| `js/views/wizard.js` + `js/wizard/` | the install wizard (`form.js` step 1, `run.js` the socket run, `views.js`, `copy.js`, `hostkey.js` shared with the dialogs, `frame.js` the bare `<dialog>`) |
| `css/terminal.css`, `prompts.css`, `wizard.css` | tokens only |

## What you call

- `views/sessions.js`: `openSession(id, {newSession, forcePassword})` (focuses an open tab for
  the connection unless `newSession`), `restoreSessions()` (main.js, once), `closeAllSessions()`
  (asks with the count; the palette offers it), `sessionCount(id)`, `viewFor(tabId)`.
- `views/wizard.js`: `openWizard(prefill, {startAt})` resolves with the last connection it saved
  or null. Prefills: `{}`, `{host, port, name}`, `{key_fp}`, `{host, port, username}` (palette),
  and `{...connection, connection_id}` with `startAt: 'signin'` (Reinstall key: the form is
  skipped and the run starts at once).
- `ui/dialogs.js`: `answerPrompt(prompt, {signal, targetName, passphraseTtl})`,
  `confirmPaste(lines, name)`, `confirmCloseAll(count)`.
- `ws/protocol.js`: `new PortKeySocket(path, {onMessage, onPrompt, onOutput, onError, onClose})`,
  `connect()`, `send()`, `sendInput()`, `close()`; `setTimings({ping})` for tests.

## Decisions (where the spec left room)

- **Cancel means stop.** The connecting overlay's Cancel and Cancel in any connection-time
  dialog close the tab (the server answers `cancelled` / `hostkey_rejected`). Keep it closed on a
  changed key leaves a panel with Try again / Close tab.
- **Dead link.** Two pings in a row with no frame of any kind back (not only `pong`) drop the
  socket and reattach; heavy output therefore never counts as a dead link. The close is
  reported as 1006.
- **Reattach.** Backoff 0.3, 1, 2, 4, 8, then 15 s, while signed in, for up to 15 minutes, then
  Disconnected. 4410 is Disconnected at once; anything else (1011, an error after `ready`)
  reattaches. `detached` (attached elsewhere) never reattaches on its own: the bar offers
  Reattach here.
- **Reload.** Open tabs are kept per browser tab in `sessionStorage["portkey.sessions"]` (session
  ids in tab order, the active one). Every remembered session is attached with `since: null`,
  listed or not, so one that ended while the page was away still shows its last output and
  exit (package 5's attach-once); a gone one shows Disconnected. Detached sessions nobody
  remembers (a closed window) are restored too; attached ones belong to another window.
- **Background tabs** open xterm on first show; their attach carries no `cols`/`rows`, and the
  first show sends `resize`. The replay after a reattach does not light the activity dot.
- **Reset.** `ready.reset` clears the terminal only on an attach; an `open` in a tab that shows
  an old session's scrollback (Reconnect) keeps it and writes a dim "Reconnecting" line.
- **Shortcut rule.** Every Ctrl+Shift combination stays out of the shell (Ctrl+Shift+F Find,
  Ctrl+Shift+C/V copy and paste as in Linux terminals); Alt+letter, Esc and Ctrl+K go to the
  shell. Showing a terminal tab (click, Alt+digit) focuses the shell unless the user is moving
  through the tab strip with the arrow keys.
- **Paste** goes through one guard, caught on the document while the terminal has focus:
  Chromium aims the paste at `<body>` after a dialog hands focus back. A trailing line break
  does not count as a line. Right-click paste uses the async clipboard API (Chrome may ask).
- **A− / A+** change every terminal and save `font_size` (one PATCH per burst).
- **Wizard.** Host key first seen is step 2 inline; the target's password and verification
  codes are step 3 inline; everything else (passphrase, changed key, a jump host's prompts) is
  a dialog naming its hop. `needs_action` shows the server's text (it carries the read-only
  reason, the Windows hint and the "already fixed" case), the client copy is the fallback.
  Failures use the spec copy, with the server's text in Details. A start the server rejects
  (`name_taken`, `group_unknown`, jump loop, `tag_invalid`, `validation`) goes back to the form.
  "Password only" saves over HTTP. "Open password session" (password expired) saves the host as
  a password connection first, since there is nothing to open otherwise. Open terminal focuses
  an open tab for that connection (Reinstall key) instead of adding one.
- **Fingerprints.** First seen shows the full fingerprint (it is compared with `ssh-keygen -lf`),
  the changed-key dialog the short form beside its strip, as in the mockups.

## Proven here (open items from phase A)

- In Chromium, `connect-src 'self'` covers a same-origin `ws://`, and a WebSocket to another
  port is refused (`test_protocol.py::test_csp_allows_same_origin_ws_and_blocks_others`).
- xterm's DOM renderer runs under the production CSP, ANSI and 24-bit colours included: this
  directory's server sends `portkey.security.headers.CSP` and fails on any violation.
- The smoke run (below) drove `/ws/terminal` and `/ws/install` in a real browser against packages
  4 to 6 and sshd with no console error.

## Test tools (`tests/frontend/terminal/`)

- `conftest.py`: `term(scenario, width, height, allow_errors)` -> Page with the mock API and the
  WebSocket mocks; records `page.problems` (console errors and CSP violations fail the test) and
  `page.dialogs` (the leave warning is a `beforeunload` dialog).
- `mock_ws.js` / `mock_install.js`: the scripted server. Hooks on `window.__pkWs`: `sockets`
  (each with `.sent`), `push`, `drop`, `exit`, `stealAttach`, `mute`, `expireToken`, `behave`,
  `inputOf`, `installs`, `attaches`. Behaviour by connection name and by wizard host is listed
  at the top of each file. Playwright's `wait_for_function` evaluates a string, which the CSP
  forbids: use `helpers.wait_until`.
- `shots.py`: screenshots at 1440 and 800 of every screen here (command in its docstring).
- `smoke.py`: the UI against the real app and sshd in the e2e network (command in its
  docstring): key, reload replay, password, first-seen key, jump host, Disconnect and Enter,
  passphrase with a wrong one first, wizard with 2FA, wizard with a password. Rerunnable.
  **The app image is built from the tree at build time:** rebuild it (`--profile e2e build app`)
  after changing the frontend, or the run tests the old files.

## Requested of other packages (none blocking)

- **8a:** `tests/frontend/serve.py` sends an old CSP without `style-src 'self' 'unsafe-inline'`;
  xterm's styles fail there. Use `portkey.security.headers.CSP` as this directory does.
- **8a:** the tab strip has one dot, so the activity dot stands in for the status dot (the
  mockup shows both). An `activity` flag on `updateTab` would allow both.
- **8a:** `main.js` swallows any error from `restoreSessions()`; a toast would make a broken
  restore visible.
- **Package 0 (wording):** `docs/ws-protocol.md` says the client treats two missed pongs as dead;
  this client counts any frame as an answer. `types.js` `WsError` could list the codes.
- **Package 9:** the flows in `smoke.py` are a ready start for the e2e specs.

## Left undone or unverified

- Not run on Windows: Edge's own Ctrl+Shift+K, the Cascadia Mono metrics, Chrome's clipboard
  prompt on the first right-click paste, the leave warning in a real browser window.
- Dead-link detection, the 15-minute give-up and reattach after an app restart are tested only
  against the mock (the first) or not at all (the other two).
- Restoring several background tabs after a reload was checked against the mock only; the smoke
  run reloads with one active tab.
- The palette's ranking weights are hand-tuned on the fictional data; no real list behind them.
- Process slip: once I invoked an empty host `python3` heredoc by mistake (no code ran, no
  file changed); every edit used the file tools and every run a container.
