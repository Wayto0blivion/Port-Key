# Handoff: package 4, SSH core

Package key `wp4-ssh-core`, 2026-11-16, on `dev/v1`. Read `docs/handoff/phase-a.md` and
`integrate.md` first; this file covers what the SSH core gives packages 5 (terminal), 6 (wizard)
and 8b (frontend terminal), the contract changes made here, and what is left open.

## State

Built: `portkey/ssh/client.py`, `hostkey.py`, `chain.py` and private modules `_hop.py` (one
hop: fetch key, decide, sign in pinned), `_errmap.py` (network and asyncssh failures to codes and
UX copy), `_compat.py` (calling a `Prompter` with the new optional keywords);
`portkey/api/ws_common.py`; `tests/unit/ssh/` and `tests/integration/ssh/`.

| Command (compose project `portkey-wp4`) | Result |
|---|---|
| `./mk check` | ruff and format clean, mypy clean, 804 unit tests pass (127 in `tests/unit/ssh`) |
| `./mk integration` | 68 pass (37 in `tests/integration/ssh`, 31 test-bed smoke tests) |
| `./mk clean` | run at the end; no containers or networks left for `portkey-wp4` |

## What you call

### Planning (blocking: call through `asyncio.to_thread`)

- `services.connector.plan(conn_id, force_password=False) -> list[HopTarget]`, outermost jump
  first. Each hop uses its own saved user, key and auth method; a `key` connection with
  `key_fp: null` gets the default key resolved here. `force_password` turns the key off for the
  target only. Raises `UnknownConnection` (4404), `KeyUnreadable` (the saved key is gone; not
  raised with `force_password`), `ProtocolError` (a cycle, or a chain deeper than 3 jumps).
- `services.connector.plan_new(jump_id, target) -> list[HopTarget]` for the wizard. Build
  `target` as `HopTarget(HopRef(0, 1, None, name, host, port, "target"), username, "key" or
  "password", key_fp or None, allow_key=..., allow_password=..., allow_kbdint=...)`; indexes,
  counts and roles are recomputed and a `None` key_fp on a `key` target becomes the default key
  (`KeyUnreadable` when there is none). Unknown `jump_id` -> `UnknownConnection` (4404 per the
  protocol). Suggested flags: try_key and verify `allow_password=False, allow_kbdint=False`;
  password_login `allow_key=False`.

### Connecting

`chain = await services.connector.connect(hops, prompter)` returns an `OpenChain`:
`chain.target` is the target's `asyncssh.SSHClientConnection`, `chain.hops` the `HopRef`s,
`await chain.close()` closes every hop, target first, and is idempotent.

It raises `SshError` subclasses (all with `hop` set), `PromptCancelled`, `PromptTimeout` and
`BadMessage` (a prompt reply missing what its kind needs). Catch `WsError` and hand it to
`ws_common.send_error`. A jump hop's network or sign-in failure is `JumpFailed` (`cause` = the
hop's own code, `message` = the hop's own text, `hop` = the jump); `hostkey_changed`,
`hostkey_rejected` and `hostkey_revoked` on a jump keep their own code and close code.

Per hop the prompter gets `status` `connecting`, `hostkey`, `auth` (once per method, with
`method` and, for publickey, `key_name`), then `ready` for the target. Host-key events
(`hostkey_accepted`, `hostkey_replaced`) are recorded here; `touch_used` and the session
events are not (package 5).

Every hop has keepalive 30 s with 3 misses. When the target dies, the jump connections stay up
until `chain.close()`: watch `chain.target.wait_closed()` and close the chain.

**Cancellation.** Run `connect` in its own task. Cancelling that task (socket gone, `close`
while connecting) aborts every hop already open and the attempt in flight; nothing keeps
connecting. A cancelled task raises `CancelledError`, so do not try to send on the socket
afterwards.

### WebSocket helpers (`portkey.api.ws_common`)

- `await authenticate(ws, services) -> bool`: accepts the socket, then needs a valid `auth`
  within 5 s. On False the `error` and close 4401 are already sent (or the client left).
- `parse_message(text) -> ClientMessage`: raises `BadMessage` that never repeats the frame. Use
  this, not `parse_client_message`, whose `ValidationError` text quotes the input (a
  `prompt_reply` carries a password).
- `await send(ws, msg)`: drops optional fields that are None and keeps required nullable ones
  (`first_seen_at`, `hop.connection_id`, `exit_status`, `signal`) as `null`.
- `await send_error(ws, exc)`: `error` then close with the matching code; generic text and close
  1011 for anything that is not a `WsError` (only its type is logged). Safe when the socket is
  already closed. `error_message(exc)` builds the `ErrorMsg` alone.
- `WsPrompter(ws, limits)`: give it to `connect`. Feed every `prompt_reply` from your receive
  loop to `prompter.deliver(reply)`; on the client's `close` call `prompter.cancel_pending()`
  (this prompt and every later one raise `PromptCancelled`). `pending_id` is the waiting prompt.
  Replies are never logged or kept.

The receive loop must keep running while `connect` waits, or replies never arrive. Sketch:

```python
prompter = WsPrompter(ws, limits)
connecting = asyncio.create_task(services.connector.connect(hops, prompter))
# receive loop: prompt_reply -> prompter.deliver(msg); close -> prompter.cancel_pending()
# socket gone -> connecting.cancel()
```

## Contract changes (all additive)

1. `interfaces.Prompter.hostkey_changed(..., *, old_key_type: str | None = None)` and
   `interfaces.Prompter.kbdint(..., *, retry: bool = False)`. The prompt messages already had
   both fields (`old_key_type` required, `retry`), but the Prompter could not be told them.
   The SSH core passes each keyword only to a prompter whose signature takes it
   (`ssh._compat.call_prompter`), so the test bed's `FakePrompter` keeps working.
2. `services.build_services` passes `vault=vault` to `ChainConnector` (new keyword-only
   constructor argument). Without it a passphrase reply with `remember: true` could not keep the
   key unlocked.
3. `api/schemas/ws.py` docstring: server messages go through `ws_common.send` (nullable rule
   above), not `model_dump_json(exclude_none=True)`, which would drop `first_seen_at: null`.
4. `docs/ws-protocol.md` decisions 12 to 17: `password_not_allowed` carries `methods`; auth
   order and when `status` `auth`/`ready` are sent; empty keyboard-interactive rounds; `retry`
   on every re-prompt; OpenSSH known_hosts port matching; incomplete replies are `bad_message`.

`types.js`, `docs/api.md` and `ssh/errors.py` needed no change.

## Decisions and gotchas

- **Two handshakes per hop.** The key is fetched with `asyncssh.get_server_host_key`, decided,
  and then the authenticated connection is opened with `known_hosts` holding only the accepted
  key, so a different key fails key exchange before any credential. sshd logs a pre-auth close
  for the fetch.
- **Multi-key servers.** The fetch offers only the algorithms of keys already trusted for that
  host:port (as OpenSSH prefers them), so an owner whose known_hosts has only the ECDSA key does
  not get a false "changed" for the ED25519 one. If the server has none of them any more, the
  key it prefers is fetched and the prompt is "changed".
- **known_hosts port matching follows OpenSSH** (decision 16). asyncssh's fallback to the
  port-less line is never used: asyncssh only ever gets explicit key lists.
- **Auth order** publickey, password, keyboard-interactive (OpenSSH asks keyboard-interactive
  first). The password prompt names user and host; keyboard-interactive is for servers that
  only offer it (2FA). Every offered method reaches a callback even when the hop does not allow
  it, which is how `methods` on `auth_failed` is filled.
- **Which error for a failed sign-in.** Wrong passwords x3 or keyboard-interactive x3:
  `auth_failed` ("rejected the password"). Key tried and refused: `auth_failed`, message
  "`<name>` refused your key." (the UX panel title). Password wanted, no key planned, and the
  server offers neither password nor keyboard-interactive: `password_not_allowed`. A
  password-change request from the server: `password_expired`.
- **Timeouts.** `limits.ssh_connect_timeout` (10 s) covers TCP and key exchange only; asyncssh's
  own `connect_timeout` would include the minutes a person spends on a prompt. The whole sign-in
  is bounded at 10 s + 120 s x 5 = 610 s (`client.login_timeout`); each prompt has its own 120 s.
- **Nothing from the container's own ~/.ssh** is loaded by asyncssh: `config=None`,
  `client_keys=None`, `agent_path=None`, `x509_trusted_certs=None`, `gss_host=None`.
- **Passphrases.** `remember: true` puts the key in the vault for `settings.passphrase_ttl`;
  `false` uses it for this connection only. `FsKeyProvider.get` rescans the key folders each
  call, so a key hop scans twice; fine for a handful of keys.
- **Logging.** `portkey.ssh` logs hop numbers, host:port, fingerprints and outcome codes;
  `portkey.ws` logs only the type of an unexpected exception. No secret or reply is logged.

## For package 6 (wizard)

- `/ws/install` has no `status` messages in the protocol, but `connect` calls
  `prompter.status(...)`. Wrap or subclass `WsPrompter` and turn `connecting`/`hostkey` into
  `step` messages (`connect` with `hop`, `hostkey`), or drop them.
- The `step hostkey` codes `known`/`trusted`/`accepted` are not reported by the connector. If
  you need them, call `ssh.hostkey.lookup` and `decide` for the target yourself before
  connecting (cheap: no network), or ask for a hook here.

## For package 7 (requested)

- `tests/fixtures/prompter.py` `FakePrompter.hostkey_changed` and `.kbdint` should take the new
  keywords (`old_key_type`, `retry`) and record them in `detail`; then `ssh._compat` can go. My
  tests use a subclass (`tests/unit/ssh/fakes.py` `KwPrompter`) meanwhile.

## Left undone or unverified

- The `@cert-authority` path (a host certificate signed by a CA in known_hosts counts as
  trusted) is implemented in `_hop._try_certificate` but never ran against a real host
  certificate: the test bed has none.
- The fetch fallback when a server no longer offers any trusted key type is not exercised
  against a real server (every test sshd has one ED25519 key).
- `password_expired` (a server password-change request) and a server-side login-grace timeout
  are only unit-tested.
- A key-exchange timeout on the authenticated connection (after a successful fetch) is not
  tested; the fetch timeout is (`test_no_ssh_banner_times_out`).
