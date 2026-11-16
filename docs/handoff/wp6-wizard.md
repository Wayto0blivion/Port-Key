# Handoff: package 6, wizard

Package key `wp6-wizard`, 2026-11-16, on `dev/v1`. Read `docs/handoff/phase-a.md`,
`integrate.md` and `wp4-ssh-core.md` first; this file covers what the wizard gives packages 8b
(frontend) and 9 (end-to-end), what the contract owner has to fold in, and what is left open.

## State

Built: `portkey/wizard/remote_script.py` (the script), `install.py` (`InstallWizard`, input
checks, save), private `_flow.py` (the run's state machine), `_steps.py` (SSH core status to
`step` messages), `_remote.py` (running commands), `_copy.py` (needs_action copy), `errors.py`
(`InputRejected`); `portkey/api/ws_install.py`; `tests/unit/wizard/`,
`tests/integration/wizard/`.

| Command (compose project `portkey-wp6`) | Result |
|---|---|
| `./mk check` (whole tree) | stops at ruff: 17 findings, all in `tests/frontend/terminal/` (package 8b, mid-edit at the time) |
| `./mk check FILES="portkey/wizard portkey/api/ws_install.py tests/unit/wizard tests/integration/wizard"` | ruff and format clean, mypy clean (whole package), 1042 unit tests pass (164 in `tests/unit/wizard`), 1 skipped (the skeleton's stub check: every route is built) |
| `./mk integration` | 90 pass (13 in `tests/integration/wizard`) |
| `./mk clean` | run at the end; nothing left for `portkey-wp6` |

## The flow

```
validate -> first connection: the key, then (same connection) password / keyboard-interactive
  key works ............................. save, outcome already_installed
  key refused, password methods offered . signed in with the password -> install
  key refused, no password methods ...... needs_action password_not_allowed (check_key)
  assume_installed: key only; refused ... needs_action key_refused (install_with_password,
                                          save_password_only) or password_not_allowed
install: `sh -s`, the script on stdin, on the password connection
  non_posix ............................. needs_action non_posix (check_key)
  readonly / failed ..................... needs_action authorized_keys_readonly
  added / already_present ............... verify
verify: a new key-only connection (through the same jump chain), `echo portkey-ok`
  refused ............................... needs_action key_still_refused
                                          (fix_permissions once, save_password_only)
  works ................................. discard_password, save, outcome installed
```

One connection does both "try the key" and "sign in with the password": the SSH core offers
publickey first, then password, then keyboard-interactive. That avoids a second host-key fetch
and a second sign-in to every jump host. The password connection stays open through verify
(fix permissions runs on it) and is closed before the save (`discard_password`), and in any
case when the run ends, fails or is cancelled.

The password never reaches wizard code: it goes from the prompt reply through `WsPrompter` and
`StepPrompter` (a pass-through) into asyncssh. Tests check that it is in no step, needs_action,
log line or event row.

## What you call

- `services.wizard.run(start, prompter, io) -> (ConnectionRecord, outcome)`. `prompter` is a
  `WsPrompter` (or any `Prompter`); `io` is a `WizardIO` (`step(msg)`,
  `needs_action(msg) -> action`). Raises `SshError` subclasses, `PromptCancelled` (also for the
  `cancel` action), `PromptTimeout`, `UnknownConnection` (4404: `connection_id` or `jump_id`
  gone), `InputRejected` (4400) and `BadMessage` (an action that was not offered).
- `/ws/install` (`api/ws_install.py`): the socket as `docs/ws-protocol.md` describes it.
  `SocketIO` is the `WizardIO` over the socket.
- `remote_script.build_install_script(line)`, `manual_install_command(line)`,
  `parse_key_line(line)` (raises `ValueError` for anything that is not a clean
  `<type> <base64> [comment]`), `parse_script_output(stdout, stderr, status)`,
  `FIX_PERMS_SCRIPT`.

## For package 8b (frontend)

What the server sends, exactly (the frontend's `wizard/copy.js` mostly matches already):

- `validate` running, then ok, or fail with the error's code (`name_taken`, `group_unknown`,
  `jump_cycle`, `jump_too_deep`, `not_found`, `key_unreadable`) followed by the `error`.
- `connect` running/ok per hop (`hop` set); `hostkey` running/ok (target only, `hop` set) with
  code `known`, `trusted` or `accepted` (no code in the rare host-certificate case). Both only
  on the first connection; verify and "Check key login" connect quietly.
- `try_key` running; ok `already_installed`, or warn `refused` (also when the server does not
  offer publickey at all, as for a password-only account).
- `password_login` running/ok; `skipped` only with `assume_installed` when the key worked.
- `install` running, then ok `added` or ok `already_present`, **then** (if loose permissions)
  a second `install` warn `perms_warning`. `copy.js` shows "Added key" for `perms_warning`
  whatever came first; keep the earlier ok's text and add the warning note instead. On a
  needs_action, `install` fails with code `non_posix` or `authorized_keys_readonly`.
- `verify` running/ok, or fail `key_still_refused` before that needs_action; after
  `fix_permissions` it runs again.
- `discard_password` ok only when a password connection existed; then `save` running/ok.
- Any `error` first marks the running step(s) `fail` with the error's code (not for
  `cancelled` or `hostkey_rejected`).
- Every `needs_action` carries `public_key` (the cleaned line) and `install_command`;
  `output` (remote output, at most 4 KB) comes with `non_posix` and
  `authorized_keys_readonly`.
- `key_still_refused` actions vary: `fix_permissions` is left out once it has been tried and
  when the server does not offer publickey at all (then the message says key logins are off).
- An `action` that arrives when nothing is waiting is ignored; `cancel` is always accepted
  (error `cancelled`, close 1000). `ping` is answered before `start` too. `close` before or
  during the run closes 1000 with no message.
- The saved connection keeps `key_fp` as sent (null = the default key).

## Requested contract changes (not made; see the package report)

1. `docs/ws-protocol.md` /ws/install: list the `InputRejected` codes (`name_taken`,
   `group_unknown`, `jump_cycle`, `jump_too_deep`, `tag_invalid`, `validation`; close 4400)
   and the step details above (the second `install` step for `perms_warning`, the fail codes,
   which steps appear when, the varying `key_still_refused` actions, stale actions ignored).
   `types.js` may want the new error codes.
2. `portkey/services.py`: `InstallWizard(connector, connections, keys, events, groups=groups)`.
   Without `groups` an unknown folder is only found at save time, after the key is installed
   (the save then fails with `group_unknown`). `known_hosts` and `host_keys` default to the
   connector's (`ChainConnector` has both), so they need no wiring.
3. Package 4: pass the host-key verdict in `status("hostkey", ...)` (for example
   `source="known_hosts"|"store"`), so `known` versus `trusted` is exact. Today the wizard
   guesses after the fact: an app record for host:port means `trusted`, else a known_hosts
   entry means `known`. Wrong only when both exist and the presented key is the known_hosts one.

## Gotchas

- **The script.** POSIX sh in a function, called as `portkey_install </dev/null`, so the shell
  parses all of it before running anything and no command can eat the rest of stdin. Markers:
  `PORTKEY_BEGIN` (missing means sh never ran: `non_posix`), `PORTKEY_ALREADY`,
  `PORTKEY_ADDED`, `PORTKEY_WARN_PERMS`, `PORTKEY_READONLY`, `PORTKEY_FAILED`. The presence
  check is awk (skips `#` lines, strips CR, matches the type and blob as fields, so options and
  other comments count); grep is the fallback when there is no awk. `printf` (not `:`) creates
  the file, because a redirection error on a special builtin would exit the shell before it
  could print `PORTKEY_READONLY`. Loose permissions come from `ls -ldL` (group or other `w`).
- **Quoting.** `parse_key_line` accepts only `[A-Za-z0-9@._-]` types, base64 whose blob names
  the same type, and cleans the comment to `[A-Za-z0-9@._ -]` (at most 100 characters). A test
  checks that every hostile comment leaves the script identical except the three quoted lines,
  and runs each under dash, bash and busybox.
- **Verify through a jump** signs in to the jump hosts again; a jump host that uses a password
  asks for it again. Rare; reusing the jump connections would need a connector API.
- **Timeouts.** The script and the verify command have 60 s each (`script_timeout`); a script
  that times out after `PORTKEY_BEGIN` is `failed` (shown as `authorized_keys_readonly`),
  before it `non_posix`. needs_action waits `wizard_idle_timeout` (10 min); the socket also
  gives up after 10 minutes without a client message (`prompt_timeout`, 4408).
- `StepPrompter` passes `old_key_type` and `retry` on only to prompters that take them, like
  `ssh._compat` (the test bed's `FakePrompter` does not).
- Tests: `tests/unit/wizard/world.py` wires the wizard to a fake connector whose attempts drive
  the prompter like the SSH core does; `fakes.py` has the attempt builders. Integration tests
  reset every user they touch in a fixture (`cleanup`), signing in as tfa with the installed
  key, so reruns against a running test bed work.

## Left undone or unverified

- Read-only `authorized_keys` is covered by the unit tests under three shells (a 0500 `.ssh`
  or 0400 file), not against sshd: the test bed has no read-only account.
- `password_expired` from the wizard is only unit-tested with the fake SSH layer (the SSH core
  produces it; see its handoff).
- `restorecon` never ran: no SELinux in any test environment.
- The `@cert-authority` path gives the `hostkey` step no code; untested, as in package 4.
- Windows OpenSSH (cmd/PowerShell) output is matched by text in the unit tests only.
