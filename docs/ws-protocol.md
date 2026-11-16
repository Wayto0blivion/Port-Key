# Port-Key WebSocket protocol (frozen contract, v1)

Models: `portkey/api/schemas/ws.py`. Close and error codes: `portkey/ssh/errors.py`. A change to
either is a contract change: write it in your package report, do not edit here.

## Transport

- Endpoints: `ws://localhost:8822/ws/terminal` and `ws://localhost:8822/ws/install`. No query
  string: tokens never go in URLs.
- The handshake needs an allowed Host and an exact allowed `Origin`; otherwise the server
  closes before accepting and the browser sees HTTP 403 (no message).
- Text frames: one JSON object with a `type`. Unknown types, unknown fields or messages out of
  order -> `error` `bad_message`, close 4400.
- Binary frames: terminal bytes (input client->server, output server->client). At most 64 KB
  per frame in either direction; a bigger frame closes the socket with 1009.
- Server messages omit fields that are null unless a field is documented as nullable.

## Auth handshake (both endpoints)

The first message must be `{"type": "auth", "token": "<bearer token>"}` within 5 seconds.

| Outcome | Server |
|---|---|
| valid | nothing; the client sends its next message |
| invalid, expired | `{"type": "error", "code": "unauthorized", "message": "Sign in to Port-Key.", "close": 4401}`, close 4401 |
| nothing in 5 s | `error` `auth_timeout`, close 4401 |

## Hops

Every `status` and `prompt` carries `hop`, and an `error` carries it when one hop failed:

```json
{"index": 0, "count": 2, "connection_id": "5a7d2e90-1c3b-4f6e-8d2a-9b0c1d2e3f4a", "name": "build-box",
 "host": "192.168.10.20", "port": 22, "role": "jump"}
```

`index` 0 is the outermost jump host; the target is `index == count - 1` with `role: "target"`.
A direct connection has `count: 1`. `connection_id` is null only for a wizard target that is
not saved yet. Hops connect in order, each through the previous one, each with its own host key
check and sign-in using that saved connection's user and key or password.

## Prompts (both endpoints)

`{"type": "prompt", "id": "p3", "kind": ..., "hop": Hop, ...}`. The client answers with
`{"type": "prompt_reply", "id": "p3", ...}`. One prompt is outstanding at a time. Any prompt may
be answered `{"type": "prompt_reply", "id": "p3", "cancel": true}`, which ends the attempt with
`error` `cancelled` and close 1000. No answer in 120 s -> `error` `prompt_timeout`, close 4408.
Replies to unknown or stale ids are ignored. Secrets in replies are never logged or echoed.

**`hostkey_new`**: first time this host:port is seen. Nothing has been sent to the host.

```json
{"type": "prompt", "id": "p1", "kind": "hostkey_new", "hop": {...target...}, "host": "192.168.10.42", "port": 22,
 "key_type": "ssh-ed25519", "fingerprint": "SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E"}
```

Reply `{"type": "prompt_reply", "id": "p1", "accept": true}`. `accept: false` -> `error`
`hostkey_rejected`, close 1000. With `hop.role == "jump"` the UI titles it "Check the jump
host's identity".

**`hostkey_changed`**: the host has trusted keys and presented a different one. No credential
has been sent.

```json
{"type": "prompt", "id": "p2", "kind": "hostkey_changed", "hop": {...}, "host": "192.168.10.42", "port": 22,
 "key_type": "ssh-ed25519", "old_key_type": "ssh-ed25519",
 "old_fingerprint": "SHA256:pZ8mQ1cWv3Lx5Ny7Oz9Ab1Cd3Ef5Gh7Ij9Kl1MnTw4E",
 "new_fingerprint": "SHA256:Rb4tY8uI2oP6aS0dF4gH8jK2lZ6xC0vB4nM8qW2eR6t",
 "first_seen_at": "2026-03-02T10:00:00Z", "confirm_name": "web-prod"}
```

Reply `{"type": "prompt_reply", "id": "p2", "accept": true, "confirm_name": "web-prod"}`. The
server replaces the key only if `accept` is true and `confirm_name` equals the prompt's
`confirm_name` (trimmed, ignoring case). Otherwise `error` `hostkey_changed`, close 4409.
`first_seen_at` is null when the old key comes from the owner's `known_hosts`.

**`passphrase`**: the hop's key is encrypted and not unlocked.

```json
{"type": "prompt", "id": "p4", "kind": "passphrase", "hop": {...}, "key_fp": "SHA256:Xk3fP0aB7cD9eF1gH2iJ4kL6mN8oP0qR2sT4uV6r9Qa",
 "key_name": "id_ed25519", "path": "C:\\Users\\john\\.ssh\\id_ed25519", "retry": false}
```

Reply `{"type": "prompt_reply", "id": "p4", "value": "...", "remember": true}`. A wrong
passphrase re-prompts with `retry: true`; after 3 wrong ones -> `error` `key_locked`, close
4502. `remember: true` keeps the key unlocked for `passphrase_ttl`; false uses it once.

**`password`**

```json
{"type": "prompt", "id": "p5", "kind": "password", "hop": {...}, "user": "john", "host": "192.168.10.42", "port": 22, "retry": false}
```

Reply `{"type": "prompt_reply", "id": "p5", "value": "..."}`. A rejected password re-prompts
with `retry: true`; after 3 -> `error` `auth_failed`.

**`kbdint`**: keyboard-interactive (2FA, OTP), relayed verbatim.

```json
{"type": "prompt", "id": "p6", "kind": "kbdint", "hop": {...}, "name": "", "instructions": "",
 "prompts": [{"text": "Verification code: ", "echo": false}], "retry": false}
```

Reply `{"type": "prompt_reply", "id": "p6", "values": ["492039"]}` (one per prompt, in order).
A server may send several rounds; each is a new prompt. An empty `prompts` list is answered
`{"values": []}`.

## /ws/terminal

### Client to server

| Message | Notes |
|---|---|
| `{"type": "auth", "token": "..."}` | first, within 5 s |
| `{"type": "open", "connection_id": "0b6f3c1e-...", "cols": 120, "rows": 40, "force_password": false}` | new shell; `force_password` = "Sign in with password this time" |
| `{"type": "attach", "session_id": "s_4Zq...", "since": 18234, "cols": 120, "rows": 40}` | reattach; `since` = bytes received so far, `null` after a page reload; `cols`/`rows` optional |
| binary frame | input bytes (ignored before `ready`) |
| `{"type": "resize", "cols": 132, "rows": 43}` | clamped to 1-1000 |
| `{"type": "ping"}` | every 25 s; the server answers `pong` |
| `{"type": "close"}` | kill the shell (or abort while connecting); the server closes 1000 |
| `{"type": "prompt_reply", ...}` | see Prompts |

The second message must be `open` or `attach`. Dropping the socket only detaches.

### Server to client

| Message | Notes |
|---|---|
| `{"type": "status", "phase": "connecting", "hop": Hop}` | phases per hop: `connecting`, `hostkey`, `auth`, then `ready` for the last hop. The SSH core's `hostkey` status also knows the trust `source` (`known_hosts` or `store`); /ws/terminal does not send it, the wizard turns it into its `hostkey` step code |
| `{"type": "status", "phase": "auth", "hop": Hop, "method": "publickey", "key_name": "id_ed25519"}` | `method`: `publickey`, `password`, `keyboard-interactive` |
| `{"type": "prompt", ...}` | see Prompts |
| `{"type": "ready", "session_id": "s_4Zq...", "connection": Connection, "offset": 0, "reset": true, "reattached": false}` | output follows as binary frames |
| binary frame | output bytes, batched about 16 ms or 64 KB |
| `{"type": "exit", "exit_status": 0, "signal": null, "reason": "closed"}` | then close 1000; `reason`: `closed` (remote closed), `lost` (keepalive failed), `killed` (close / DELETE) |
| `{"type": "detached", "reason": "attached_elsewhere"}` | another socket attached; then close 1000; do not reattach automatically |
| `{"type": "pong"}` | |
| `{"type": "error", "code": "...", "message": "...", "close": 4502, "hop"?, "methods"?, "cause"?}` | then close with `close` |

`Connection` is the object from `docs/api.md`.

**Replay.** Each session keeps the last 256 KB of output, addressed by absolute byte offset.
`ready.offset` is the absolute offset of the first byte that follows. On `open`, `offset: 0,
reset: true`. On `attach` with `since` inside the buffer, `offset == since`, `reset: false`: the
client appends. With `since: null`, or `since` older than the buffer, `reset: true` and `offset`
is the first byte sent: the client clears the terminal and writes the replay. That is the oldest
byte held, or, once the buffer has wrapped, up to 4 KB later: the replay is trimmed to start
after a line feed, at an ESC, or after UTF-8 continuation bytes, so it never begins inside a
character or an escape sequence. A `since` beyond what was sent is treated as "everything
sent". The client counts received bytes as `offset + bytes since ready` and sends that as the
next `since`.

Sessions live on detached for `detach_grace` (15 min); then they are closed. A session whose
shell ended while it was detached is not listed by `GET /api/sessions` and does not count toward
the 32, but it can still be attached once until the grace runs out: the attach gets `ready`, the
last output and the `exit`; after that it is gone (4410). At most 32 sessions; `open` beyond
that -> `too_many_sessions`, 4429. SSH keepalive every 30 s, 3 misses end the session with
`exit` `lost`. The client sends `ping` every 25 s and treats two pings in a row with no frame of
any kind back (not only `pong`: under heavy output a `pong` can sit behind output frames) as a
dead socket, and reattaches. Port-Key never reconnects SSH on its own.

### Example: through a jump host, first-seen target key

```
C {"type":"auth","token":"q3Jx..."}
C {"type":"open","connection_id":"0b6f3c1e-...","cols":120,"rows":40}
S {"type":"status","phase":"connecting","hop":{"index":0,"count":2,"name":"build-box","role":"jump",...}}
S {"type":"status","phase":"hostkey","hop":{"index":0,...}}
S {"type":"status","phase":"auth","hop":{"index":0,...},"method":"publickey","key_name":"id_ed25519"}
S {"type":"status","phase":"connecting","hop":{"index":1,"count":2,"name":"web-prod","role":"target",...}}
S {"type":"prompt","id":"p1","kind":"hostkey_new","hop":{"index":1,...},"host":"192.168.10.42","port":22,...}
C {"type":"prompt_reply","id":"p1","accept":true}
S {"type":"status","phase":"auth","hop":{"index":1,...},"method":"publickey","key_name":"id_ed25519"}
S {"type":"status","phase":"ready","hop":{"index":1,...}}
S {"type":"ready","session_id":"s_4Zq...","connection":{...},"offset":0,"reset":true,"reattached":false}
S <binary "Welcome to Ubuntu 24.04...">
```

A failing jump: `{"type":"error","code":"jump_failed","cause":"timeout","hop":{"index":0,"name":"build-box",...},"message":"Couldn't reach 192.168.10.20 on port 22. The connection timed out after 10 seconds.","close":4502}`.

Auth failure: `{"type":"error","code":"auth_failed","methods":["publickey","password"],"hop":{...},"message":"web-prod refused your key.","close":4502}`.

## /ws/install

Same auth handshake, prompts and `ping`/`pong`. The wizard ends with `done` or `error`.

### Client to server

```json
{"type": "start", "host": "192.168.10.42", "port": 22, "username": "john", "key_fp": null, "name": "web-prod",
 "jump_id": null, "group_id": "d4e5f6a7-1829-4b0c-9d8e-7f6a5b4c3d2e", "tags": ["prod"], "color": "red",
 "pinned": false, "notes": "", "assume_installed": false, "connection_id": null}
```

`key_fp: null` = the default key. `assume_installed` = "My key is already on this host" (skip
the password step). `connection_id` updates that saved connection instead of creating one
(Reinstall key). Then: `{"type": "action", "action": "..."}` after a `needs_action`,
`prompt_reply`, `ping`, and `close` (abandon; the password is dropped).

### Server to client

`{"type": "step", "step": "...", "state": "running|ok|warn|fail|skipped", "code"?, "detail"?, "hop"?}`

| Step | Codes on `ok`/`warn`/`skipped` | UX line |
|---|---|---|
| `validate` | | key readable, input valid |
| `connect` | | "Reaching ..." per hop (`hop` set) |
| `hostkey` | `known` (matches known_hosts, step 2 skipped), `trusted` (app store), `accepted` | step 2 |
| `try_key` | `already_installed` (ok -> save, outcome `already_installed`), `refused` (warn) | |
| `password_login` | `skipped` with `assume_installed` | "Signed in with password" |
| `install` | `added`, `already_present`, `perms_warning` (warn) | "Added key to ~/.ssh/authorized_keys" / "Key was already in authorized_keys" |
| `verify` | | "Signed in again with the key only" |
| `discard_password` | | "Password discarded" |
| `save` | | connection saved |

`{"type": "needs_action", "code": "...", "message": "...", "actions": [...], "public_key"?, "install_command"?, "output"?}`
then the server waits (up to 10 minutes) for `{"type": "action", "action": "..."}`. `output` is
remote output for the collapsed Details block (at most 4 KB). `cancel` is always accepted and
ends with `error` `cancelled`, close 1000.

| `code` | When | `actions` |
|---|---|---|
| `key_refused` | `assume_installed` and key login refused | `install_with_password`, `save_password_only`, `cancel` |
| `password_not_allowed` | server offers neither password nor keyboard-interactive | `check_key`, `cancel` (plus client-side copy buttons) |
| `key_still_refused` | key added but verify failed (permissions) | `fix_permissions`, `save_password_only`, `cancel` |
| `authorized_keys_readonly` | signed in but could not write the file | `save_password_only`, `cancel` |
| `non_posix` | no `sh` (Windows, restricted shells) | `check_key`, `cancel` |

`check_key` retries key-only login (success -> save, outcome `already_installed`).
`fix_permissions` runs `chmod go-w` on `~`, `~/.ssh` and the file, then verifies again.
`save_password_only` saves with `auth_method: "password"` (outcome `saved_password_only`).

`{"type": "done", "connection": Connection, "outcome": "installed|already_installed|saved_password_only"}`, then close 1000.

`error` as for /ws/terminal: `dns`, `refused`, `timeout`, `unreachable`, `jump_failed`,
`hostkey_changed` (4409), `hostkey_rejected`, `auth_failed` (3 wrong passwords),
`password_expired`, `key_locked`, `key_unreadable`, `cancelled`, `prompt_timeout` (4408),
`not_found` (4404, unknown `jump_id` or `connection_id`), `bad_message`.

A `start` that passes the schema but cannot be saved ends with `error` and close 4400 carrying
the HTTP API's code for the same problem: `name_taken`, `group_unknown`, `jump_cycle`,
`jump_too_deep`, `tag_invalid` or `validation` (an unknown `jump_id` is `not_found`, 4404). The
`message` is ready to show beside the form.

### Step details

What the server sends, beyond the table above:

- `validate` running, then ok, or fail with the error's code followed by the `error`.
- `connect` (per hop, `hop` set) and `hostkey` (target only) appear only on the first
  connection of a run; verify and "Check key login" connect quietly. `hostkey` ok has code
  `known`, `trusted` or `accepted`; no code for a host certificate.
- `try_key` ok `already_installed`, or warn `refused`, also when the server does not offer
  publickey at all (a password-only account).
- `password_login` `skipped` appears only with `assume_installed` when the key worked.
- `install` ok `added` or ok `already_present`, then, if permissions are loose, a second
  `install` warn `perms_warning` (keep the first line's text and add the warning). Before a
  `needs_action`, `install` fails with code `non_posix` or `authorized_keys_readonly`, and
  `verify` fails with `key_still_refused`.
- `discard_password` appears only when a password connection existed.
- Any `error` first marks the running steps `fail` with its code (not for `cancelled` or
  `hostkey_rejected`).
- Every `needs_action` carries `public_key` and `install_command`; `output` comes with
  `non_posix` and `authorized_keys_readonly`. `key_still_refused` leaves out `fix_permissions`
  once it has been tried and when the server offers no publickey. `assume_installed` with a
  key-only server that refuses the key gives `password_not_allowed`, not `key_refused`.
- An `action` with nothing waiting is ignored; `ping` before `start` is answered; `close`
  before `start` closes 1000.
- The saved connection keeps `key_fp` as sent in `start` (null = the default key).

### Example: fresh host with 2FA

```
C {"type":"auth","token":"q3Jx..."}
C {"type":"start","host":"192.168.10.42","port":22,"username":"john","key_fp":null,"name":"web-prod"}
S {"type":"step","step":"validate","state":"ok"}
S {"type":"step","step":"connect","state":"ok","hop":{...}}
S {"type":"step","step":"hostkey","state":"ok","code":"known"}
S {"type":"step","step":"try_key","state":"warn","code":"refused"}
S {"type":"step","step":"password_login","state":"running"}
S {"type":"prompt","id":"p1","kind":"password","hop":{...},"user":"john","host":"192.168.10.42","port":22,"retry":false}
C {"type":"prompt_reply","id":"p1","value":"..."}
S {"type":"prompt","id":"p2","kind":"kbdint","hop":{...},"name":"","instructions":"","prompts":[{"text":"Verification code: ","echo":false}]}
C {"type":"prompt_reply","id":"p2","values":["492039"]}
S {"type":"step","step":"password_login","state":"ok"}
S {"type":"step","step":"install","state":"ok","code":"added"}
S {"type":"step","step":"verify","state":"ok"}
S {"type":"step","step":"discard_password","state":"ok"}
S {"type":"step","step":"save","state":"ok"}
S {"type":"done","connection":{...},"outcome":"installed"}
```

## Close codes

| Code | Error code(s) | Meaning |
|---|---|---|
| 1000 | (none), `cancelled`, `hostkey_rejected` | exit, done, client close, detached elsewhere, user cancel |
| 1009 | | frame over 64 KB |
| 1011 | `internal` | unexpected server error |
| 4400 | `bad_message` | malformed or out-of-order message |
| 4401 | `unauthorized`, `auth_timeout` | auth failed or missing |
| 4403 | | Origin not allowed (normally a 403 before accept) |
| 4404 | `not_found` | unknown connection (or jump/connection id in `start`) |
| 4408 | `prompt_timeout` | prompt unanswered for 120 s |
| 4409 | `hostkey_changed` | host key changed and not replaced |
| 4410 | `session_gone` | attach to a session that no longer exists |
| 4429 | `too_many_sessions` | 32 sessions already open |
| 4502 | `dns`, `refused`, `timeout`, `unreachable`, `auth_failed`, `password_not_allowed`, `password_expired`, `key_locked`, `key_unreadable`, `hostkey_revoked`, `protocol`, `jump_failed` | SSH failure |

## Decisions

1. Every `status` and `prompt` carries `hop`, even for a direct connection (`count: 1`), so the
   client has one code path. The architecture made it optional.
2. User cancellation (prompt `cancel`, `close` while connecting, declining a first-seen key)
   closes with 1000 and code `cancelled` / `hostkey_rejected`; it is not a failure.
3. `key_locked` means 3 wrong passphrases; `auth_failed` after 3 wrong passwords or when every
   allowed method failed, with `methods` listing what the server offered.
4. `error` carries the close code that follows as `close`.
5. `ready` carries `offset`, `reset` and `reattached`, so replay after a gap or a reload is
   unambiguous; the architecture only defined `since`.
6. `exit` carries `reason` (`closed`, `lost`, `killed`) for the two Disconnected messages.
7. `attach` may carry `cols`/`rows`; `open` may carry `force_password`.
8. The typed host-key name is compared trimmed and ignoring case; the prompt sends the
   expected name as `confirm_name`. Replacing a key whose old record is in the owner's
   `known_hosts` adds the new key to Port-Key's store; `known_hosts` is never edited, so the
   old entry also stays trusted.
9. The wizard reports progress with fixed step names and codes, and asks for decisions with
   `needs_action` / `action` instead of extra prompt kinds. Key already installed saves and
   finishes immediately (outcome `already_installed`), as PLAN.md says; the UI's
   [Save connection] there just moves to the final card.
10. `start` accepts `group_id`, `tags`, `pinned`, `notes` and `connection_id` so the wizard
    can save a complete connection or reinstall a key on an existing one.
11. Close codes 4400 (bad message) and 4429 (too many sessions) were added.

Decided in package 4 (SSH core, 2026-11-16):

12. `error` `password_not_allowed` carries `methods` too (what the server offered), as
    `auth_failed` does.
13. Sign-in order per hop: the hop's key, then password, then keyboard-interactive. A
    `status` `auth` is sent once per method per hop, when that method starts; the SSH core
    sends `status` `ready` for the target once the last hop has signed in.
14. A keyboard-interactive round with no prompts and no name or instructions is answered by
    the server itself; a round with only text is relayed as `kbdint` with `prompts: []`.
15. A wrong passphrase, password or keyboard-interactive answer re-prompts with `retry: true`;
    `kbdint.retry` is true on the first round of a new attempt.
16. known_hosts matching follows OpenSSH: for a port other than 22 only `[host]:port` lines
    match, never the port-less `host` line (asyncssh's own matcher would fall back to it).
17. A reply that lacks what its prompt needs (`accept`, `value`, or one `values` entry per
    question) is `bad_message`, close 4400.

Decided in package 9 (end to end, 2026-11-16), folding in what packages 5, 6 and 8b reported:

18. On `reset: true`, `ready.offset` is the first byte sent, which may be up to 4 KB after the
    oldest byte held (the replay starts at a clean boundary). A session that ended while
    detached can be attached once more for its last output and `exit`.
19. The client counts any frame, not only `pong`, as an answer to its `ping`.
20. /ws/install: the input-rejected codes (close 4400) and the step details above.
21. The SSH core decides the host key before `status hostkey` and passes the trust `source`
    to its prompter; the wizard's `hostkey` code comes from it instead of a guess.
