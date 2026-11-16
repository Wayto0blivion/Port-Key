# Handoff: package 5, terminal

Package key `wp5-terminal`, 2026-11-16, on `dev/v1`. Read `phase-a.md`, `integrate.md` and
`wp4-ssh-core.md` first. This file covers what the terminal gives packages 8b (frontend terminal)
and 9 (end-to-end), the decisions taken where `docs/ws-protocol.md` left room, and what is open.

## State

Built: `portkey/sessions/ringbuffer.py`, `terminal.py`, `registry.py` and private modules
`_shell.py` (the PTY channel and asyncssh callbacks) and `_replay.py` (where a full replay may
start); `portkey/api/ws_terminal.py`, `routes_sessions.py`; `tests/unit/sessions/` and
`tests/integration/sessions/`.

| Command (compose project `portkey-wp5`) | Result |
|---|---|
| `./mk check` | stops at lint on package 8b's in-progress `tests/frontend/terminal/` (9 ruff errors, 2 files to reformat); my paths are clean |
| `./mk types` | mypy clean on the whole tree (92 files) |
| `./mk unit` | 1041 pass, 1 skip (75 in `tests/unit/sessions`, 5 runs in a row green) |
| `./mk integration` | 89 pass (9 in `tests/integration/sessions`; mine also 3 runs in a row green) |
| `./mk clean` | run at the end |

No JavaScript, CSS or contract file was touched.

## What you call

### Registry (`services.sessions`, `MemorySessionRegistry`)

- `await create(connection_id, cols, rows, prompter, force_password) -> TerminalSession`: plans
  in a thread, connects through `prompter`, starts the shell, registers. Raises
  `TooManySessions` (4429) before connecting when 32 live sessions plus attempts in flight
  exist, `UnknownConnection`, and everything `connect` raises.
- `get(session_id)` (raises `SessionGone`, 4410), `await kill(session_id)` (raises
  `NotFound(session_gone)`, 404), `list_all()` (live sessions, oldest first),
  `await connection_of(session)` (the saved connection now, or the one from open time if it was
  deleted since), `await reap_once()`, `start()`, `aclose()`.
- Keyword-only constructor extras: `clock` (monotonic), `wall` (UTC now), `reap_interval`
  (5 s). `services.py` is unchanged.
- Events: `session_opened` with `{host, port}` and `touch_used` after a successful open;
  `session_closed` with `{outcome: closed|lost|killed}` (a reaped session is `killed`). Both run
  in a thread in the background, never on the event loop and never delaying `ready`.

### Session (`TerminalSession`)

`attach(sink, since)`, `detach(sink)`, `write(data)`, `resize(cols, rows)` (clamped 1..1000),
`close(reason)`, `summary()`, `has_sink(sink)`, plus `reading_paused`, `unsent`, `ended`,
`exit`. `SessionSink` (in `terminal.py`) now has `ready(offset, reset)` and `lagged()` besides
`output`, `exited` and `detached`: the session's pump sends `ready` itself, so nothing can reach
the socket before it.

### Routes

`GET /api/sessions` and `DELETE /api/sessions/{id}` are `async` on purpose: the registry lives on
the event loop and must not be touched from the thread pool. DELETE of an attached session sends
it `exit` `killed` and close 1000.

## Behaviour the frontend (8b) and e2e (9) can rely on

- **Order on open:** `status` and `prompt` messages from the SSH core, `status ready` for the
  target, then `ready`, then binary output. Output produced between the shell starting and
  `ready` (the motd) is in the replay, so `ready` on open is `offset: 0, reset: true`.
- **No loss, no duplicate.** Replay and live output come from the ring buffer as one stream per
  attachment; attaching picks the start offset and registers the cursor with no await between.
  `since` inside the buffer -> `reset: false`, `offset == since`. `since` beyond what was sent ->
  `offset` = everything sent, `reset: false`.
- **Full replay start (`reset: true`) of a buffer that has wrapped** is trimmed so it does not
  begin inside a UTF-8 sequence or an escape sequence: within the first 4 KB it starts after the
  first line feed, else at the first ESC, else after any UTF-8 continuation bytes
  (`sessions/_replay.py`). `ready.offset` is the first byte *sent*, which can be up to 4 KB
  after the oldest byte held; the client's byte count stays exact. A buffer that never wrapped
  replays from offset 0 untrimmed. Terminal modes set before the replay window (alternate
  screen, colours of a full-screen program) are not restored: such a program looks partial
  until it redraws.
- **Batching:** frames of at most 64 KB; less than a frame waits about 16 ms to gather more, so
  keystroke echo can lag by up to 16 ms.
- **Takeover:** the old socket gets `detached` (after any frame it was already sending), then
  close 1000; whatever it still sends is ignored (it can neither type into nor `close` the
  session it lost).
- **Exit:** remote exit -> the rest of the output, then `exit` (`closed`, with `exit_status` or
  `signal`), close 1000. Keepalive or network loss -> `lost`. `close`/DELETE/reaper -> `killed`,
  sent at once without flushing what was still unsent.
- **A session that ends while detached** stays registered (not listed by `GET /api/sessions`,
  not counted in the 32) until the grace runs out, so a reattach with its id still gets `ready`,
  the last output and the `exit`. Its exit delivered, it is gone (4410 afterwards).
- **Before `ready`:** binary frames are dropped; the second message must be `open` or `attach`
  (a `ping` first is `bad_message`, as the protocol says). While connecting, `ping`, `resize`
  (applied once the shell is up), `prompt_reply` and `close` work.
- **Errors after `ready`** (`bad_message`) end the socket; the session stays, detached.
- An input frame over 64 KB: close 1009 (uvicorn's `ws_max_size`, with a handler check as a
  backstop). The session stays, detached.
- `lagged`: if an attached sink ever fell out of the buffer, the socket is closed with 1011 and
  no `error` message, so the client reattaches and gets `reset: true`. It cannot happen with SSH
  (see below); it exists as a guard.

## Backpressure policy

- While a socket is attached and more than 3/4 of the ring buffer (192 KB) is unsent, the
  session stops reading the SSH channel (`pause_reading`); below 1/4 it resumes. asyncssh then
  holds at most one channel window (2 MiB) and stops granting window, so the remote program
  blocks at the browser's speed, as in a real terminal. Each read is at most one SSH packet
  (32 KB), so the cursor never falls out of the 256 KB buffer.
- Sending waits on uvicorn's flow control (the sans-I/O WebSocket `send` waits until the
  transport drains). Per session the server holds at most: the 256 KB buffer, one 2 MiB channel
  window, one 64 KB frame and the transport's buffer, so 32 stalled sessions stay under about
  80 MB.
- A detached session never pauses: it keeps reading and the buffer keeps the newest 256 KB, as
  the protocol says. A detached `yes` therefore keeps a CPU core busy until the reaper ends it.
- Every session has its own pump task; a stalled browser or a flood never blocks another
  session. Tests: a flood of 400 000 lines arrives complete while another session's echo and
  the flooding socket's `ping` both answer (asserted within 3 s; the whole flood takes about
  2 s); a non-reading browser holds a 30 MB writer back with the buffer bounded, and every byte
  arrives once it reads again.
- Input: `write` waits while asyncssh has more than 256 KB of input buffered for a remote that
  is not reading (a huge paste into a stalled program). The socket's receive loop waits too, so
  `ping` answers are delayed in that case.

## Gotchas

- **Compression.** Browsers and the `websockets` client negotiate permessage-deflate with
  uvicorn, so repetitive output (`yes`) costs almost nothing on the wire and backpressure starts
  much later. The slow-browser integration test turns compression off on purpose.
- **Testing the socket.** `tests/unit/sessions/live.py` runs the real app under uvicorn on a
  127.0.0.1 socket inside the test container (nothing published) and gives a `TermClient`
  (`websockets` client sending `Host: localhost:8822` and the matching `Origin`). Package 9 or 6
  can reuse it.
- **Typed commands echo.** In tests, wait for a marker the shell computes (`echo x-$((6*7))`),
  never for text that is in the command line itself; bash also prints `ESC[?2004l\r` (bracketed
  paste off) before the first line of output.
- `tests/unit/skeleton/test_app.py::test_stub_route_answers_501_envelope` now always skips: no
  stub route is left.

## Requested of other packages

- **Package 9:** `tests/e2e/test_shell_seams.py::test_unbuilt_routes_degrade_quietly` expects
  `GET /api/sessions` to answer 501; it now answers 200 `{"sessions": []}`. That spec fails
  until it is changed.
- **Package 0 (contract wording):** in `docs/ws-protocol.md` "Replay", say that on `reset: true`
  `offset` is the first byte sent, which may be a little after the oldest byte held (trimmed to
  a clean start), and that a session which ended while detached can still be attached once to
  receive its output and `exit`. Both are additive; no schema changes.

## Left undone or unverified

- `exit` `lost` is unit-tested with a fake channel only; no test cuts the network under a live
  session to trip the real 30 s x 3 keepalive.
- The reaper is tested with a fake clock (and its background loop with a short interval), not
  with a real 15-minute grace.
- The `lagged` guard path is unit-tested only (it needs a read bigger than the buffer's
  headroom, which SSH packets never are).
- No browser has driven `/ws/terminal` yet (8b and 9), and permessage-deflate's CPU cost on a
  long flood was not measured.
