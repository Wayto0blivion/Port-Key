# Phase A handoff: notes for the packages that come next

Collected at the phase A integration (2026-11-16) from the final reports of packages 1
(security), 2 (storage), 3 (keys), 7 (test infrastructure) and 8a (frontend shell). Every
note those engineers left for later packages is here in full, grouped by the package it is for.
A note that concerns several packages appears under each of them. Where the integration
changed something a note describes, the note is followed by an *Integration:* line.

What the integration itself changed, and how to run things now, is in
`docs/handoff/integrate.md`.

## Open decisions (waiting on the owner)

- **First-visit setup code.** Package 1 asked for a one-time setup code on
  `POST /api/auth/setup`, printed to the container log at first start (as Jupyter does), with a
  field for it on the first-visit form. Reason: first-visit setup has no authentication, so any
  local process that reaches 127.0.0.1:8822 before the owner does (another Windows account, a
  WSL distro, local software, all in the threat model) can set the password, take a token and
  use the owner's SSH keys. The Origin check stops browser pages from doing this, but not local
  processes. **Not implemented**: it changes `docs/api.md`, `SetupRequest`, `docs/ux-spec.md`
  and the first-run screen, and the owner has not decided. Nobody should build it until they do.

## Packages 4, 5 and 6 (SSH core, terminal, wizard)

- (package 2) Threading and SQLite: I followed the db.py/interfaces.py pattern, which is one
  sqlite3 connection behind a threading.RLock with synchronous repositories. Reads that take
  more than one statement also go through db.tx(), so they see one consistent snapshot. The
  pattern is acceptable but not free. An async caller on the event loop, such as touch_used, a
  host-key store, or events.record during connect or a session, blocks the loop while a
  thread-pool route holds the lock. Measured in the test container: bulk edit of 1000 ids about
  51 ms (it was 116 ms before I made it set-based), list_all of 1000 connections about 21 ms, a
  single create about 2 ms. busy_timeout=5000 could also block the loop for up to 5 s if another
  process ever holds the database file. Packages 4 and 5: call repositories through
  asyncio.to_thread on hot async paths, as the reachability route already does for list_all.
  *Integration:* this is now the rule for everyone (`portkey/interfaces.py` header,
  `docs/work-packages.md`). It applies to package 6 too. `db.tx()` is re-entrant on one
  thread, so several repository calls made inside one `asyncio.to_thread` function can share a
  transaction (`with services.db.tx(): ...`), as `PATCH /api/keys/{id}` now does.
- (package 2, packages 4 and 6) resolve_chain(conn_id) returns records with the outermost jump
  first. validate_jump(None, jump_id) checks a jump for a connection that is not saved yet,
  which suits the wizard. touch_used never changes updated_at. ConnectionsRepo.create normalises
  tags, strips name, host and username, and raises Invalid(validation) for bad fields, so the
  wizard can pass ws 'start' values straight through.
- (package 2) EventLog.record rejects unknown kinds. It drops detail keys whose names look like
  secrets (pass, secret, token, private, otp, answer, value, data, input, output) and logs only
  the dropped key names. Use keys such as host, user, port, fp, old_fp, new_fp, outcome.
- (package 3, packages 4 and 6) portkey.keys.discovery.load_private_from_disk(record,
  passphrase) bypasses the vault. pick_default(records, settings.default_key_fp) applies the
  same default rule without a rescan.
- (package 1) Test authors: every non-GET request needs Origin, X-Port-Key: 1 and Content-Type:
  application/json, DELETE included. httpx's client.delete() cannot send a body, so use
  client.request('DELETE', url, json={}). The root client fixture already sets Origin and
  X-Port-Key.
- (package 1) tests/unit/security/conftest.py has raw-ASGI drivers (http_call, ws_call) and a
  Spy downstream app, for exact-header tests that httpx would normalise away.
- (package 7) Import the test bed from tests.fixtures: testbed (USERS, the
  SSHD/SSHD_ALT_PORT/SSHD_REKEYED/SSHD_INNER servers, CLIENT_KEYS, KEY_PASSPHRASE, TOTP_SECRET,
  APP_PASSWORD, hostkey_fingerprint(), Totp), prompter (FakePrompter, CANCEL, kbdint_answers)
  and sshclient (ssh_connect, load_client_key, reset_user). See tests/fixtures/README.md.
- (package 7) Integration fixtures in tests/integration/conftest.py: sshd, sshd_alt_port,
  sshd_rekeyed, sshd_inner, ssh_users, hostkey_fingerprints, hostkey_publics, totp,
  client_keys, key_passphrase, fake_prompter (a factory), fixture_ssh_dir (a 0777 copy),
  portkey_config (a Config built on that copy), ssh_connect and reset_ssh_user. Every test under
  tests/integration is marked integration automatically.
- (package 7) Each sshd container keeps its state for the whole run. After a wizard test
  installs a key, call `await reset_ssh_user('fresh')`; pass server= or tunnel= for the other
  sshds. nosh cannot be reset.
- (package 7) tfa's keyboard-interactive login is two rounds: 'Password: ' (pam_unix), then
  'Verification code: ' (TOTP). PasswordAuthentication is off for tfa; publickey is allowed
  alone, so the wizard's verify step works.
- (package 7) Fixture known_hosts trusts set A for sshd, [sshd]:2222 and sshd-inner (hashed).
  sshd-rekeyed is deliberately absent, so first-seen and changed-key tests seed the app store
  themselves.

## Package 4 (SSH core)

- (package 3) FsKeyProvider.load_private(fp, passphrase) returns the vault copy if held.
  Otherwise: no passphrase on an encrypted key raises ssh.errors.KeyLocked, a wrong passphrase
  raises errors.BadRequest(code=bad_passphrase), and a missing, damaged or mismatched file
  raises KeyUnreadable. It does not put the key in the vault: call services.vault.put(fp, key,
  ttl) yourself when the prompt reply has remember=true (ttl = settings.passphrase_ttl). It is
  blocking, so run it in a thread.
- (package 3) FileKnownHosts.lookup(host, port) returns KnownHostsMatch. Its keys are 'type
  base64' with no comment, and revoked keys are removed from trusted. FileKnownHosts also has
  is_trusted(host, port, public_key) and is_revoked(...), which accept a key line with or
  without a comment. To compare yourself, normalise the presented key with
  key.export_public_key('openssh') and drop the comment. The file is re-read when its mtime,
  size or inode changes.
- (package 3) An encrypted PEM or PKCS8 key with no .pub file next to it cannot be listed. It
  appears under problems as 'unreadable' with a hint to add the .pub file. Encrypted OpenSSH
  keys list without a .pub file, but their comment is then empty because it is stored inside
  the encrypted part.
- (package 2) HostKeyStore.add/replace accept OpenSSH public-key text with or without a
  comment. They store 'type base64' and compute fp_sha256 themselves. Hosts are lower-cased.
  replace checks the new key before it deletes anything.
- (package 1, ws_common.authenticate) Host and Origin are already enforced before the handler
  runs. Validate the first-message token with services.tokens.verify(token), or
  services.auth.authenticate('Bearer ' + token); both never raise for garbage and both slide the
  expiry. *Integration:* both also enforce the new absolute 90-day token lifetime.
- (package 7, packages 3 and 4) asyncssh's known_hosts match() falls back to the port-less entry
  when no [host]:port entry matches (OpenSSH does not). For example, sshd on port 2200 matches
  the plain 'sshd' line.
- (package 7) keyonly has every fixture key pre-seeded except port-key/id_ed25519_portkey (use
  that one as the 'unknown key'). badperms has ~/.ssh at 0777, so a key added there is refused
  until chmod go-w. nosh signs in fine, but every command exits 1 with 'This account is
  currently not available.'

## Package 5 (terminal)

Everything under "Packages 4, 5 and 6" applies, the threading note first: the session hot path
(touch_used, events.record on open and close) must not call SQLite on the event loop.

- *Integration:* `tests/unit/skeleton/test_app.py` checks the 501 envelope on the first of
  `GET /api/sessions` and `DELETE /api/sessions/{id}` that is still a stub and skips once both
  are built, so implementing them needs no change there.

## Package 6 (wizard)

Everything under "Packages 4, 5 and 6" applies; for the test bed, the reset, tfa and
known_hosts notes there matter most.

- (package 7) keyonly has every fixture key pre-seeded except port-key/id_ed25519_portkey (use
  that one as the 'unknown key'). badperms has ~/.ssh at 0777, so a key added there is refused
  until chmod go-w. nosh signs in fine, but every command exits 1 with 'This account is
  currently not available.'
- *Integration:* the dev image has dash, bash and busybox on PATH for the remote-script unit
  tests; `tests/unit/skeleton/test_dev_image.py` checks all three run.

## Package 8b (frontend terminal)

- (package 8a) Terminal tabs: call addTab({id, title, closable: true, color: connection.color,
  render(panel), onActivate(panel), onClose}). Use updateTab(id, {title, color, muted, dot})
  with dot set to 'connecting', 'connected', 'disconnected', 'activity' or null. Closing a tab
  focuses the tab on its left. The panel is position:absolute inset:0 with overflow:auto; set
  overflow:hidden on your own content. Add the class lbl-<color> to get --lbl for the 2px
  terminal frame.
- (package 8a) Sessions: call store.setSessionInfo({counts: {connectionId: n}, connected: n})
  whenever sessions change. This feeds the sidebar badges, the 'Connected now' text on Recent
  cards and the leave warning. Sidebar rows, the Home table, Recent cards and the row menus call
  views/sessions.js openSession(id, {newSession}). main.js calls restoreSessions() after the
  shell renders.
- (package 8a) Wizard: Add connection, suggestion [Add] and Keys [Install on a host…] call
  openWizard(prefill). Prefill is {} or {host, port, name} or {key_fp}. The store refreshes when
  openWizard resolves with a connection. For the wizard's Folder and Tags fields, reuse
  ui/folderpicker.js createFolderPicker({groups, value, onChange, id}) and pickerValue(el), and
  ui/tagfield.js createTagField({value, suggestions, onChange, id}) with commitTagField(el)
  before saving. parts/fields.js has field() and colorSwatches().
- (package 8a) Dialogs: use ui/modal.js openModal({title, body, buttons, danger, width,
  initialFocus, onAction, dismissible}). It is a native <dialog> with showModal(). onAction may
  return false to keep the dialog open, and setError() shows a message in it. When danger is
  set, the safe button gets focus first. The sign-in dialog stacks above any open modal after a
  401. Shared CSS classes are listed at the top of dialogs.css and layout.css.
- (package 8a) Palette: the Ctrl+Shift+K, Ctrl+Shift+Space and Ctrl+K (outside the terminal)
  shortcuts and the top-bar field call ui/palette.js openPalette(). For commands, use
  shell.openToolTab('keys' | 'settings'), views/keys.js openGenerateKey() and
  shell.lockAllKeys(). Global shortcuts are ignored while any dialog[open] exists, and the
  terminal counts as focused when focus is inside .xterm.
- (package 8a, test tools) tests/frontend/run.sh test runs the shell suite; run.sh shots
  /abs/out [names] writes screenshots at 1440 and 800 wide. The mock picks its scenario from
  window.__PK_MOCK_SCENARIO or ?mock= (normal, empty, nokey, ppk, many, setup, signedout, error,
  slow). The app password is 'correct horse battery' and the token is 'mock-token'.
  *Integration:* `./mk frontend P=<project>` runs the same suite through compose (no network);
  `FRONTEND=tests/frontend/terminal` narrows it. Your browser tests go in
  `tests/frontend/terminal/` and reuse `tests/frontend/conftest.py`; the mock has no WebSocket,
  so put a WebSocket mock of your own there.
- (package 2) GET /api/groups lists root folders first, then the others grouped by parent id,
  each group in sort_order then name. Build the tree on the client. A new folder gets sort_order
  = max of its siblings + 1. A failed PATCH on a connection with an explicit null for a field
  that cannot be empty (name, host, port, color, pinned, tags) returns 422 'validation'.
- (package 3) GET /api/hosts/suggestions already leaves out saved hosts (matched by host or
  alias at the same port) and dismissed ones, so saved is always false. Dismissing by an alias
  also hides the merged suggestion. GET /api/keys returns default_fp as the effective default
  key: the one from settings, or id_ed25519, id_ecdsa, id_rsa, then name order. Problem details
  are short copy such as 'PuTTY format, not supported' and 'Public key only. The private key is
  not in this folder.' *Integration:* the `saved` field is gone from the API and types.js.
- (package 1) Rate-limit message: RateLimited carries retry_after, and the message reads 'Too
  many attempts. Try again in N seconds.' Wrong login password: 401 bad_password, 'That password
  is not correct. Try again.'
- (package 1) Stricter than the letter of api.md, on purpose: a GET/HEAD/OPTIONS that carries a
  foreign Origin is refused with 403 (a browser only sends a foreign Origin when a foreign page
  made the request); a request with more than one Host, Origin, X-Port-Key or Content-Type header
  is refused; Content-Type 'application/json; charset=utf-8' is accepted.
- (package 1, unfinished) Not checked in a real browser: that Chrome/Edge treat connect-src
  'self' as covering ws://localhost:8822 (CSP Level 3 says they should), and that xterm renders
  under the CSP. *Integration:* still unchecked; 8b sees it first, 9 must prove it.

## Package 9 (end-to-end)

- (package 7) Once any test uses the app_token fixture, the app password is set for the rest of
  the run (data is in a tmpfs). A first-run spec must not use app_token and must sort first,
  e.g. test_00_first_run.py. Failing e2e tests leave a screenshot in test-results/. The harness
  self-tests live in tests/e2e/harness/, not in tests/e2e/test_*.py.
- (package 7) The evil_origin fixture returns {origin, ws: {opened, error, closeCode, messages},
  fetch: {blocked, status}}. Against the current guarded app it gave ws.opened False, closeCode
  1006 and fetch.blocked True. AppApi.raw(..., host='evil-origin:8080') returned 421.
- (package 7, for 8a and 9) The e2e UI helpers find the login controls by the ux-spec labels
  'Password' and 'Confirm password' and the buttons 'Set password' and 'Sign in'. The token goes
  in localStorage under 'portkey.token'.
- (package 7) ./mk e2e builds the app image from the working tree at that moment, so another
  package's half-finished edit can stop the app from starting.
- (package 7) Docker Hub returned 429 during my builds. The evil-origin base image is pinned by
  digest to a locally cached nginx 1.31.8.
- (package 3, packages 7 and 9) keygen writes only to config.keys_dir. Names are checked against
  existing names ignoring case. known_hosts, authorized_keys, config and rc are refused as key
  names (key_name_invalid). Encrypted keys use 64 bcrypt rounds, so unlocking takes about 0.6 s.
  *Integration:* the name check only looks in the generated-keys folder, so `id_rsa` is free
  there although `.ssh\id_rsa` exists; in the e2e app `/ssh/port-key` and `/keys-generated`
  are different folders, unlike on the PC. The frontend mock now behaves the same way.
- (package 3) An encrypted PEM or PKCS8 key with no .pub file next to it cannot be listed (see
  package 4); the fixture id_rsa_pass has its .pub for that reason.
- (package 1) Starlette's ServerErrorMiddleware sits outside the user middleware (frozen part of
  app.py), so the 500 responses it produces do not carry the security headers. This is minor.
- (package 1) Stricter than the letter of api.md, on purpose: a GET/HEAD/OPTIONS that carries a
  foreign Origin is refused with 403 (a browser only sends a foreign Origin when a foreign page
  made the request); a request with more than one Host, Origin, X-Port-Key or Content-Type header
  is refused; Content-Type 'application/json; charset=utf-8' is accepted.
- (package 1) Test authors: every non-GET request needs Origin, X-Port-Key: 1 and Content-Type:
  application/json, DELETE included. httpx's client.delete() cannot send a body, so use
  client.request('DELETE', url, json={}). The root client fixture already sets Origin and
  X-Port-Key.
- (package 1, unfinished) Confirm in a real browser that connect-src 'self' covers
  ws://localhost:8822 and that xterm renders under the CSP.
- (package 7, unfinished) Diagnostics in the e2e app report ssh_dir.writable = true. To get the
  Windows-like 0777 modes, the fixture .ssh is copied into a writable tmpfs; on the real PC the
  mount is read-only and reports false. A spec that checks ssh_dir.writable must expect true in
  e2e.
- (package 8a) 9: tests/frontend/run.sh test runs the shell suite; run.sh shots /abs/out
  [names] writes screenshots at 1440 and 800 wide. The mock picks its scenario from
  window.__PK_MOCK_SCENARIO or ?mock= (normal, empty, nokey, ppk, many, setup, signedout, error,
  slow). The app password is 'correct horse battery' and the token is 'mock-token'.
- (package 8a, unfinished) I have not run the frontend against the real backend. Package 9
  should do a smoke test against the app container once packages 1 to 3 are in the image.
  *Integration:* done: `tests/e2e/test_shell_seams.py` (yours now) drives sign-in, sidebar,
  Home, edit and bulk tagging, Keys, Settings, Diagnostics, the 501 routes and the 401 path, and
  fails on any page error, console error or unexpected /api failure. See
  `docs/handoff/integrate.md` for what it found.

## Every package

- (package 1) Where the contract weakens these defences: (a) GET/HEAD/OPTIONS are exempt from
  the Origin/CSRF checks, so every owner must keep GET routes free of side effects;
  (b) unauthenticated first-visit setup (see "Open decisions"); (c) behind Docker Desktop's port
  proxy every client has the same peer address, so the per-client backoff is effectively global
  and a local process can keep the owner out of login for up to 10 minutes at a time;
  (d) PORTKEY_ALLOWED_HOSTS trusts each listed name for both Host and Origin, so listing a LAN
  name or 0.0.0.0 would expose that name to DNS rebinding (matching is exact, with no
  wildcards); (e) tokens live in localStorage, so any XSS means token theft. A later hardening
  step could add Trusted Types (require-trusted-types-for 'script') once the frontend avoids
  innerHTML.
- (package 3) Process slip: I once ran a host python3 one-liner to edit text in a test file (a
  string replacement, no project code). Do not: edit with the file tools, run code only in
  containers.

## Notes for packages already built (acted on at the integration)

- (package 2, for package 3) KeysMetaRepo.set_hidden(fp, True) also clears settings
  default_key_fp in the same transaction when that key is the default. set_label and set_hidden
  raise NotFound(key_unknown) unless upsert_seen has seen the fingerprint. To make
  hide-with-reassign atomic, wrap both calls in one `with services.db.tx():`, since tx() is
  re-entrant on the same thread: connections.reassign_key(...) then keys_meta.set_hidden(...).
  reassign_key accepts key_fp=None with auth_method 'key', meaning the default key; with
  'password' it forces key_fp to None. *Integration:* done in `routes_keys.update_key`, with a
  test that a failure at the last write changes nothing.

## Unfinished items from the phase A reports

| From | Item | Status after the integration |
|---|---|---|
| 1, 2, 3, 7, 8a | Whole-tree `./mk check` failed (lint in package 7 files at the time, then the skeleton 501 test) | Fixed: green |
| 2 | The route tests swap in a fake key provider, so key_unknown was never checked against the real FsKeyProvider | Fixed: `test_key_unknown_with_the_real_provider` |
| 3 | Hiding a key with reassign was three transactions | Fixed: one transaction |
| 1 | connect-src 'self' covering ws://localhost:8822, and xterm under the CSP, unchecked in a real browser | Open: 8b, then 9 |
| 7 | Diagnostics in the e2e app report ssh_dir.writable = true | Open by design (see package 9) |
| 8a | Never run against the real backend | Done, and the seams it found are fixed |
| 8a | The Home table rebuilds all rows on each data change (about 150 ms for 320 connections in the headless container); no virtualization | Open |
| 8a | Screenshots swap the container's font for Liberation Sans; the Segoe UI look on Windows is unchecked | Open: windows smoke |
| 8a | Folders move with the Move menu but cannot be dragged (only connections can, as the spec asks) | Open, not required |
