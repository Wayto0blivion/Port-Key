# Port-Key architecture

This is the build specification for Port-Key v1. It is written for engineers who have not seen
any earlier discussion. Where this document and `docs/PLAN.md` disagree, this document wins.
The screens, states and copy are specified in `docs/ux-spec.md`.

## 1. What Port-Key is

A browser-based SSH connection manager for one person. It shows saved SSH connections as a GUI.
Clicking one opens that host in a terminal inside the browser (xterm.js). It prefers key
authentication; passwords are a fallback and are never stored.

Fixed facts:

- The backend runs on the owner's own Windows PC, in a container under Docker Desktop (WSL2
  backend). The browser is on the same PC.
- The PC's `C:\Users\<user>\.ssh` folder is mounted into the container. The PC's private key is
  used directly and never leaves the PC.
- Development happens on a Linux dev box. Every build, test, lint and type check runs inside a
  container, never on the host. The finished app runs on the Windows PC with `docker compose up`.

### In v1

- Saved connections; click to open an in-browser terminal; several sessions as tabs.
- Key authentication with the PC's existing key; password authentication as a fallback.
- Keyboard-interactive / 2FA prompts relayed to the browser.
- Show and copy public keys.
- Key install wizard (the `ssh-copy-id` equivalent), including through a jump host.
- Host suggestions from `known_hosts`.
- Key generation in the app.
- Jump hosts (one saved connection reached through another).
- App login with a single app password.
- Sessions that survive a dropped WebSocket or a page reload.
- Duplicate connection, notes, colour label, nested folders (up to 3 levels), tags, bulk move
  and tag, pinned connections, default user.
- Scrollback Find, multi-line paste guard, reachability dots.
- Diagnostics endpoint and view; change password and forget host key in Settings.

### Not in v1

Connect-once from the palette, PWA install, split panes, automatic SSH reconnect, SFTP, port
forwarding, snippets, session logging, export/import, `~/.ssh/config` import, agent-only keys,
PuTTY `.ppk` keys, connection pooling, light theme, deleting key files.

## 2. Stack

Python, FastAPI and vanilla JavaScript with no build step. The size problem of earlier tools
(one 2,300-line server file, one 4,700-line script) is solved by small modules, not by a
framework.

| Layer | Choice | Approximate pin |
|---|---|---|
| Runtime | `python:3.12-slim` | 3.12 |
| Web | FastAPI, `uvicorn[standard]` | fastapi >=0.115,<1; uvicorn >=0.32 |
| SSH | asyncssh with the bcrypt extra | asyncssh >=2.18; cryptography >=43 |
| Models | pydantic v2 | 2.x |
| Storage | stdlib `sqlite3` in WAL mode, no ORM | - |
| Frontend | Native ES modules; vendored `@xterm/xterm` 5.5.0, `@xterm/addon-fit` 0.10.0, `@xterm/addon-search` | DOM renderer (Playwright can read its text) |
| Dev tools | pytest 8, pytest-asyncio, httpx, ruff, mypy, Playwright for Python (`mcr.microsoft.com/playwright/python`), eslint on `node:22` | Dependencies locked with hashes (`uv lock` or `pip-compile --generate-hashes`) |

Why asyncssh and not paramiko:

- **Concurrency.** It runs on asyncio, so many terminals do not mean one blocked thread each.
  Keepalive is built in (`keepalive_interval`).
- **Auth prompts.** Auth callbacks (keyboard-interactive, password) can be coroutines, so a 2FA
  or password prompt can wait for a browser round trip in the middle of the handshake.
- **known_hosts.** It parses hashed entries, `[host]:port`, `@cert-authority` and `@revoked`
  (`import_known_hosts` / `match_known_hosts`).
- **Key generation.** `generate_private_key("ssh-ed25519")` plus
  `export_private_key("openssh", passphrase)` produces files Windows OpenSSH can use.
- **Encrypted keys.** It reads ed25519, RSA and ECDSA in OpenSSH, PEM and PKCS8 formats,
  encrypted or not.
- **Tunnels.** `tunnel=` on `connect()` gives jump hosts without an external `ssh` binary.

## 3. Windows and Docker Desktop

### Mounts

| Host path | Container path | Mode | Purpose |
|---|---|---|---|
| `${PORTKEY_SSH_DIR:-${USERPROFILE}/.ssh}` | `/ssh` | read-only | The owner's keys and `known_hosts`. Nothing can damage them. |
| `${USERPROFILE}/.ssh/port-key` | `/keys-generated` | read-write, create-only | Keys generated in Port-Key. Windows `ssh -i ~/.ssh/port-key/<name>` can use them. |
| Named volume `portkey-data` (Docker's name: `portkey_portkey-data`) | `/data` | read-write | SQLite database and app trust records. Backup and restore: README. |

Rules:

- SQLite never lives on a Windows bind mount (locking and speed problems over the file-sharing
  layer). It lives in the named volume only.
- Writes to `/keys-generated` are create-new (`O_EXCL`) plus atomic rename. The app never
  overwrites and never deletes any key file, in either mount. There is no delete-key feature;
  a key can only be hidden from Port-Key (a flag in `keys_meta`).
- A key may be visible under both mounts (`/ssh/port-key/...` and `/keys-generated/...`).
  Keys are deduplicated by fingerprint.
- Docker creates the `port-key` host folder if it is missing, and new files inherit the `.ssh`
  folder's ACLs, which Windows OpenSSH accepts. (Unverified, see `docs/windows-smoke.md`.)
- The UI always shows Windows paths, never container paths. The compose file passes the host
  path of the `.ssh` folder to the app as an environment variable for display.

### File permissions

Bind-mounted `.ssh` files appear with mode 0777 in the container. The app never shells out to
`ssh` or `ssh-keygen`, and asyncssh does not check file modes, so this is only an informational
line in diagnostics. If an OpenSSH binary is ever needed, the key is first copied to a tmpfs
file with mode 0600.

### Keys the app cannot use

- **Keys that exist only in the Windows ssh-agent.** The agent's named pipe
  (`\\.\pipe\openssh-ssh-agent`) cannot be reached from a Linux container, and Docker Desktop
  for Windows has no agent passthrough (unverified, see `docs/windows-smoke.md`). Diagnostics
  explain this and suggest generating a key in Port-Key and installing it with the wizard.
- **PuTTY `.ppk` keys.** Reported as unsupported with the same suggestion.

### Passphrase-protected keys

- The passphrase is requested over the WebSocket prompt protocol.
- The decrypted key is held only in an in-memory `KeyVault` with a TTL (default 8 hours) and a
  "Lock keys" action. It is never written to disk.
- To show a public key, the `.pub` file is used. If it is missing, the cleartext public-key
  block of the OpenSSH-format private key file is read.

### Text files (CRLF and encodings)

One function, `textio.read_text_normalized()`, handles every key and `known_hosts` read. It:

- strips a UTF-8 BOM;
- decodes UTF-16 when it sees a `FF FE` or `FE FF` BOM (PowerShell 5 `>` writes that);
- turns CRLF into LF.

Anything the app writes is LF-terminated.

### Networking

All of this is unverified, see `docs/windows-smoke.md`:

- Docker Desktop NATs container traffic through the Windows network stack, so LAN hosts and
  VPN / Tailscale 100.x addresses are reachable from the container.
- Tailscale MagicDNS names and entries in the Windows `hosts` file may or may not resolve.

Mitigations that are built regardless:

- `POST /api/diag/resolve` shows what the container resolves for a name.
- The compose file documents `extra_hosts:` for names that do not resolve.
- Connections accept raw IP addresses.
- The PC itself is reachable as `host.docker.internal`.

### Port

The compose file publishes `127.0.0.1:8822:8000`. uvicorn binds `0.0.0.0` inside the container.

### Container hardening

- Non-root uid 1000. `/data` is chowned in the image so a new named volume inherits it.
- `read_only: true`, tmpfs `/tmp`, `cap_drop: [ALL]`, `no-new-privileges`.
- `mem_limit: 1g` and `pids_limit: 256`, so a runaway Port-Key cannot take the whole Docker
  Desktop VM.
- A healthcheck.

## 4. Threat model and mitigations

### What is protected

- Shells on the owner's servers.
- Private keys (the app can read them) and keys decrypted in memory.
- The host-key trust store.

### Attackers in scope

- Malicious websites open in the same browser.
- Other origins on localhost, such as dev servers.
- Other Windows users or processes using loopback (other accounts, WSL distros, local software).
- A network attacker between the container and the servers.
- Hostile output from a server.

Malware running as the owner is out of scope: it can read `.ssh` directly.

### Mitigations

**Host header allowlist.** The first middleware, applied to HTTP and WebSocket. Allowed:
`localhost:PORT`, `127.0.0.1:PORT`, `[::1]:PORT`. Anything else gets 421 or 400. This stops DNS
rebinding.

**Exact Origin check.**

- Every WebSocket handshake must carry an Origin from the same list. If it does not, the server
  closes before accepting, which yields HTTP 403. This stops cross-site WebSocket hijacking.
- Every non-GET request must also carry a matching Origin.

**No cookies.** Cookies are not separated by port, and SameSite treats `localhost:3000` as the
same site. Instead:

- A bearer token kept in `localStorage` (scoped to the exact origin including the port), sent as
  `Authorization: Bearer`.
- WebSockets authenticate with the first message `{type:"auth", token}`. Tokens never go in
  URLs, because uvicorn logs query strings.
- With no ambient credentials, CSRF has nothing to ride on. In addition, every mutating request
  must be `Content-Type: application/json` and carry `X-Port-Key: 1`.

**Request body cap.** A local process can fake every header a browser cannot, and FastAPI
reads the whole body before auth runs. So a middleware inside the host guard refuses any HTTP
body over 256 KB with 413 `payload_too_large`: at once when `Content-Length` says so, and as
soon as a chunked body passes the cap, without reading the rest. WebSocket frames are capped
at 64 KB by uvicorn.

**App login.** Required.

- One app password, set on first visit, hashed with scrypt.
- **First-visit setup code.** Setting the first password needs no sign-in, so without more,
  any local process that reaches the port before the owner does (another Windows account, a
  WSL distro, local software: all in scope above) could set the password and then use the
  owner's SSH keys. The Origin check stops web pages, not local processes. So while no password
  exists, the app holds a one-time setup code (`security/setup_code.py`):
  - generated at startup with `secrets` (12 symbols from a 32-symbol alphabet without 0/O and
    1/I, 60 bits, shown as `XXXX-XXXX-XXXX`), regenerated at every start until a password is
    set, and held in memory only (never in the database, an event or a response);
  - printed once, in one framed record on the `portkey.setup` logger, which `__main__` sends to
    the container log at INFO whatever the log level. Reading `docker compose logs portkey`
    needs control of Docker on the PC, which a process merely reaching the port does not have.
    The redaction filter scrubs the code from every other record as a backstop;
  - required by `POST /api/auth/setup`, compared in constant time (SHA-256 of both sides, then
    `hmac.compare_digest`), in any case and with or without separators. Wrong codes count
    against the same per-client backoff as wrong passwords, so 2^60 codes cannot be tried;
  - dropped once the password is set; later starts print nothing.
  - `python -m portkey.reset_password` deletes the password while the app runs, but the running
    app holds no code (a password existed at its start), so setup is refused with
    `setup_needs_restart` until a restart prints a fresh code. A reset never reopens setup to
    whoever reaches the port first.
  - Left open (owner decision): behind Docker Desktop's port proxy every client has the same
    address, so all clients share one backoff record. A local process that sends one wrong
    password or code each time a lockout ends re-locks for up to 10 minutes every time, and
    can keep the owner out of setup and of sign-in (a new browser, after logout, after the
    30/90-day expiry) for as long as it keeps going; a restart clears the record, but the
    process can simply resume. It cannot set or learn the password, and tokens already issued
    keep working. Any throttle shared by every client can be held this way; a shorter one
    trades the lockout for faster guessing. The principled fix needs something only the
    owner has, for example an unlock code printed to the container log (as the setup code is)
    that lets a sign-in through a lockout.
- Failed logins get per-client exponential backoff (see "Left open" above).
- Login returns a bearer token valid for 30 days, sliding (each use extends it), and never
  longer than 90 days after it was issued. Only the SHA-256 hash of each token is stored, in
  SQLite, so sessions survive restarts.
- The password can be changed in Settings; changing it revokes all other tokens. Open
  WebSockets re-check their token every 10 seconds and close (4401) once it is revoked or
  expired, so a stolen token loses its open shells too.

**Response headers.**

- `Content-Security-Policy: default-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'`.
  `style-src 'self' 'unsafe-inline'` is added only if xterm needs it.
- `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`.

**Hostile server output.** The clipboard (OSC 52) addon is not loaded, nor the web-links addon.
xterm.js 5.5 shows OSC 8 hyperlinks on its own; Port-Key's `linkHandler` (`terminal/links.js`)
ignores a plain click, and on Ctrl/Cmd+click shows its own dialog with the link's real host and
full address before opening it (http and https only) with `noopener,noreferrer`.

**Passwords and passphrases.**

- They travel only inside a WebSocket prompt reply.
- They are never persisted and never echoed back, and the reference is dropped after use.
- FastAPI's default 422 handler echoes the request input, so a custom handler strips the
  `input` field everywhere.

**Host keys.**

- Before connecting, the server's host key is fetched with `asyncssh.get_server_host_key`.
- Entries in the owner's `known_hosts` (`/ssh/known_hosts`) count as trusted. New trust
  decisions go into the app database. The app never writes the owner's `known_hosts`.
- **Unknown key:** a `hostkey_new` prompt shows the SHA256 fingerprint and key type. On accept,
  the key is stored. The connection then proceeds with `known_hosts` pinned to the accepted keys.
- **Changed key** (the host has trusted records and the presented key is not among them): the
  connection stops before any credential is sent. A `hostkey_changed` prompt shows the old and
  new fingerprints. The stored key is replaced only if the reply carries `accept: true` and a
  `confirm_name` equal to the connection's name; the server validates the typed name. Declining
  or a mismatch closes with 4409.
- **Replacing a `known_hosts` key** stores the new key and marks the old `known_hosts` key(s)
  for that host as distrusted in the app database. The file is not edited, but Port-Key no
  longer accepts the old key there without the changed-key prompt (it may be the compromised
  one). The dialog says so and gives the `ssh-keygen -R` command for Windows ssh.
- **Trust is kept per route.** App records are scoped by the jump connection the host is
  reached through (none: directly), because one address behind two jump hosts can be two
  machines. `known_hosts` applies on every route, as in OpenSSH, except where a replacement
  distrusted its key.
- Keys marked `@revoked` are always refused.
- Stored host keys (and distrusts) can be forgotten from Settings.
- Each hop of a jump chain gets its own host key check.

**Never logged.** Passwords, passphrases, keyboard-interactive answers, terminal input and
output, private key bytes, tokens and WebSocket payloads. The setup code is logged exactly once,
in its startup announcement, and nowhere else. The asyncssh logger is set to WARNING and a
redaction filter is a backstop. Allowed in logs: connection id, host, user, fingerprints and
outcome codes.

## 5. Data model

One SQLite file, `/data/portkey.db`, WAL mode, migrations tracked by `PRAGMA user_version`.

| Table | Columns | Notes |
|---|---|---|
| `connections` | id (uuid), name (unique ignoring case), host, port, username, auth_method (`key` or `password`), key_fp (nullable; null means the default key), jump_id (nullable, references `connections.id`), group_id (nullable, references `groups.id`; null means ungrouped), color, pinned, notes, sort_order, created_at, updated_at, last_used_at, use_count | One folder per connection; tags through `connection_tags`. |
| `groups` | id (uuid), name, parent_id (nullable, references `groups.id`), sort_order, created_at | Folders. At most 3 levels; names unique among siblings ignoring case. |
| `tags` | id, name (unique, lower-case, `[a-z0-9][a-z0-9._-]{0,31}`), created_at | Created on first assignment; a trigger deletes a tag when its last use goes. |
| `connection_tags` | connection_id, tag_id | Primary key on both; cascades on delete of either side. |
| `keys_meta` | fp (SHA256, primary key), label, last_path, source (`ssh_dir` or `generated`), hidden, created_at | Keys are discovered by scanning on every list call; this table only holds labels, the hidden flag and provenance. |
| `host_keys` | id, host, port, via (jump connection id, '' when direct), key_type, public_key (OpenSSH text), fp_sha256, distrusted, first_seen_at, last_seen_at | Unique on (host, port, via, public_key). `distrusted` marks a replaced known_hosts key (schema version 2). |
| `dismissed_suggestions` | host, port, dismissed_at | known_hosts suggestions the owner dismissed. |
| `app_auth` | scrypt salt and hash | One row. |
| `tokens` | sha256(token), created_at, expires_at, last_used_at | 30-day sliding expiry, capped at created_at + 90 days. |
| `settings` | key, value (JSON) | default_key_fp, default_user, passphrase_ttl, detach_grace, font size, copy/paste mode, leave warning. |
| `events` | ts, kind, connection_id, detail | Small audit trail: host key accepted or replaced, key installed, session opened. Capped at 5,000 rows. |

### Jump host rules

- `jump_id` points at another saved connection. Chain depth is at most 3 hops before the target.
- Cycles are rejected on save.
- A connection that other connections use as a jump cannot be deleted until its dependants are
  re-pointed; the API returns a conflict naming them.
- Each hop authenticates with its own saved connection's settings (user, key or password).

### Folder and tag rules

- Folders (the API calls them groups) nest at most 3 levels. Moving a folder under itself or a
  descendant is rejected, as is a move that would push anything below level 3.
- Deleting a folder moves its child folders and connections to its parent (the root /
  Ungrouped when it has none). It never deletes connections.
- Tag input is trimmed, a leading `#` is dropped and it is lower-cased. Renaming a tag onto an
  existing name merges them. A tag disappears with its last use.
- Folder collapsed/expanded state is per-browser UI state in `localStorage`, not in the
  database. Filtering and search are client-side.

## 6. HTTP API

`docs/api.md` is the frozen contract with examples for every endpoint; where it adds detail to
this section (wrapped list responses, extra endpoints), it wins.

Every error has the shape `{error:{code,message}}`. All endpoints need the bearer token except
auth status, setup and login.

### Auth

| Method and path | Request | Response |
|---|---|---|
| `GET /api/auth/status` | - | `{setup, authenticated, setup_code}` (`setup_code` says whether the code is needed, never what it is) |
| `POST /api/auth/setup` | `{password, code}` | `{token}` (only while no password is set, and only with the setup code from the container log) |
| `POST /api/auth/login` | `{password}` | `{token}` |
| `POST /api/auth/logout` | - | - |
| `POST /api/auth/password` | `{old, new}` | - |

### Connections

| Method and path | Request | Response |
|---|---|---|
| `GET /api/connections` | - | `{connections:[Connection]}` |
| `POST /api/connections` | `{name, host, port=22, username, auth_method, key_fp?, jump_id?, group_id?, tags?, color?, pinned?, notes?}` | `Connection` (carries `group_id` and `tags: [str]`) |
| `PATCH /api/connections/{id}` | Any subset of the above | `Connection` |
| `DELETE /api/connections/{id}` | - | 409 with dependants if it is used as a jump |
| `POST /api/connections/{id}/duplicate` | - | `Connection` |
| `POST /api/connections/{id}/restore` | - | `Connection` (Undo of a delete, same id) |
| `POST /api/connections/bulk` | `{ids:[...], set_group_id?: uuid or null, add_tags?: [str], remove_tags?: [str]}` | `{connections:[updated]}`; atomic |
| `POST /api/connections/reachability` | `{ids:[...]}` | `{results:[{id, state: up|down|skipped, ms?}]}` |

Reachability is a TCP connect to the SSH port. Connections that use a jump are `skipped`.

### Folders and tags

| Method and path | Request | Response |
|---|---|---|
| `GET /api/groups` | - | `{groups:[{id, name, parent_id, sort_order, connection_count}]}`, flat; counts include descendants |
| `POST /api/groups` | `{name, parent_id?}` | `Group` |
| `PATCH /api/groups/{id}` | `{name?, parent_id?, sort_order?}` | `Group` |
| `DELETE /api/groups/{id}` | - | - (contents move to the parent) |
| `GET /api/tags` | - | `{tags:[{name, count}]}` |
| `PATCH /api/tags/{name}` | `{name}` | `{name, count}`; renames everywhere, merging if the new name exists |
| `DELETE /api/tags/{name}` | - | - (removed from every connection) |

### Keys

| Method and path | Request | Response |
|---|---|---|
| `GET /api/keys` | - | `[{fp, type, bits, comment, path, source, encrypted, unlocked, public_key, is_default, hidden, used_by}]` |
| `GET /api/keys/{fp}/public` | - | text/plain |
| `POST /api/keys/generate` | `{name, type: ed25519|rsa|ecdsa, bits?, comment, passphrase?}` | key object |
| `PATCH /api/keys/{fp}` | `{label?, hidden?}` | key object |
| `POST /api/keys/{fp}/unlock` | `{passphrase, remember}` | - |
| `POST /api/keys/lock` | - | - |

`path` is the Windows path for display.

### Hosts

| Method and path | Request | Response |
|---|---|---|
| `GET /api/hosts/suggestions` | - | `{suggestions:[{host, port, aliases, key_types}], hashed_count}`; saved and dismissed hosts left out |
| `POST /api/hosts/suggestions/dismiss` | `{host, port}` | - |
| `GET /api/hostkeys` | - | list of stored host keys |
| `DELETE /api/hostkeys/{id}` | `{confirm_host}` | - |

Only plain-text `known_hosts` entries can be listed; wildcard entries are skipped. Hashed
entries are counted and still match when the user types a host. Entries that share a key are
merged into one suggestion.

### Sessions, settings, diagnostics

| Method and path | Request | Response |
|---|---|---|
| `GET /api/sessions` | - | `[{session_id, connection_id, started_at, attached}]` |
| `DELETE /api/sessions/{id}` | - | - |
| `GET /api/settings` / `PATCH /api/settings` | settings subset | settings |
| `GET /api/diag` | - | mounts, writability, key problems, version |
| `POST /api/diag/resolve` | `{host}` | what the container resolves and whether the port answers |

## 7. WebSocket protocol

### `/ws/terminal`

**Client to server**

- The first message must be `{type:"auth", token}` within 5 seconds.
- Then `{type:"open", connection_id, cols, rows}` or `{type:"attach", session_id, since}`.
- Binary frames carry input bytes (64 KB cap per frame).
- `{type:"resize", cols, rows}` (clamped to 1-1000).
- `{type:"ping"}`.
- `{type:"close"}` kills the shell. Simply dropping the socket only detaches.
- `{type:"prompt_reply", id, ...}` answers a prompt.

**Server to client**

- `{type:"status", phase: connecting|hostkey|auth|ready, hop?}`.
- `{type:"prompt", id, kind, hop?, ...}`. `hop` names the connection the prompt belongs to when
  a jump chain is in use. Kinds:

| Kind | Fields | Reply |
|---|---|---|
| `hostkey_new` | `{host, port, key_type, fingerprint}` | `{accept}` |
| `hostkey_changed` | `{host, port, key_type, old_fingerprint, new_fingerprint, first_seen_at}` | `{accept, confirm_name}` |
| `passphrase` | `{key_fp, path, retry}` | `{value, remember}` |
| `password` | `{user, host, retry}` | `{value}` |
| `kbdint` | `{name, instructions, prompts:[{text, echo}]}` | `{values:[]}` |

  Any reply may be `{cancel:true}`. A prompt times out after 120 seconds.

- `{type:"ready", session_id, connection}`.
- Binary frames carry output. The server batches reads for about 16 ms or up to 64 KB per frame.
- `{type:"exit", exit_status, signal}` followed by close 1000.
- `{type:"detached", reason:"attached_elsewhere"}`.
- `{type:"pong"}`.
- `{type:"error", code, message}` followed by a close code.

| Close code | Meaning |
|---|---|
| 4401 | Auth failed or missing |
| 4403 | Origin not allowed |
| 4404 | Unknown connection |
| 4408 | Prompt timed out |
| 4409 | Host key changed and not replaced |
| 4410 | Session gone |
| 4502 | SSH failure |

SSH failure codes carried in `error.code`: `dns`, `refused`, `timeout`, `auth_failed`,
`password_not_allowed`, `key_locked`.

**Keepalive**

- SSH keepalive every 30 seconds with a limit of 3 missed replies.
- uvicorn WebSocket ping 20 s / 20 s.
- The client sends `ping` every 25 seconds and treats 2 missed pongs as a dead link.

**Detach and reattach**

- Each session keeps a 256 KB output ring buffer addressed by absolute byte offset.
- When the WebSocket drops, the session stays alive, detached, for `detach_grace` (default
  15 minutes).
- The client reattaches with `since` set to the number of bytes it has received, which resumes
  without a gap. After a page reload it sends `since: null` and gets a full replay into a fresh
  terminal.
- If the SSH connection itself dies, the client shows the Disconnected state. Reconnect opens a
  new shell. SSH is never reconnected automatically.

**Sessions**

- At most 32 sessions.
- Each session attaches to one WebSocket at a time; a new attach takes it over and the old
  socket receives `detached`.
- One SSH connection (plus its tunnel hops) per terminal session. There is no connection
  pooling.
- PTY: `term_type=xterm-256color`, bytes mode (`encoding=None`).

**Jump chains**

- The server resolves the chain from `jump_id`, outermost hop first.
- Each hop is connected with asyncssh, the next with `tunnel=<previous connection>`.
- Each hop runs its own host key check and its own authentication, using that saved
  connection's settings. Prompts and status messages carry `hop` so the UI can say which host
  is asking.
- Closing the session closes every hop it opened.

### `/ws/install`

Same auth handshake and prompt sub-protocol as `/ws/terminal`. In addition:

- Client: `{type:"start", host, port, username, key_fp, name, jump_id?, group_id?, tags?, color?, assume_installed?}`
  (full message in `docs/ws-protocol.md`, which is the frozen contract for both sockets).
- Server: `{type:"step", step, state: running|ok|warn|fail, detail}`.
- Server: `{type:"done", connection}` at the end.

## 8. Key install wizard

1. **Validate input.** Make sure the chosen key's public key can be read.
2. **Check the host key** (section 4). Unknown keys need confirmation. A changed key stops the
   wizard unless it is replaced by typed confirmation. With a jump, the hops are connected
   first, each with its own check.
3. **Try the key first.** If key-only auth already works, report "already installed", save the
   connection and finish. This is the first idempotency check.
4. **Log in with the password.** Password and keyboard-interactive are allowed; 2FA or OTP
   prompts are relayed through `kbdint` prompts.
   - If the server offers neither method, fail with `password_not_allowed` and show the public
     key and the manual command for `~/.ssh/authorized_keys`.
5. **Run `sh -s` and feed the script on stdin.** The login shell only has to parse `sh -s`, so
   fish, csh and zsh all work.
   - The key line is single-quoted. Its comment is cleaned to `[A-Za-z0-9@._ -]` and contains
     no CR.
   - Script outline:
     - `umask 077`;
     - create `~/.ssh` with `mkdir -m 700` if missing; create `authorized_keys` with
       `chmod 600` if missing;
     - `grep -qF '<type> <base64>'`, printing `PORTKEY_ALREADY` if found;
     - otherwise add a newline if the file does not end in one (`[ -n "$(tail -c1 f)" ]`);
     - `printf '%s\n' '<line>' >>`;
     - run `restorecon` if it exists;
     - check for group/other-writable home, `.ssh` or file with `find -perm -022` and print
       `PORTKEY_WARN_PERMS`;
     - print `PORTKEY_ADDED`.
   - Existing modes are not changed silently. On a permissions warning the UI offers "Fix
     permissions" (`chmod go-w`).
   - **Non-POSIX remotes.** If `sh` is missing (Windows OpenSSH runs cmd or PowerShell;
     restricted or forced-command shells), report "non-POSIX remote, not supported". For
     Windows admin accounts, show the manual path
     `C:\ProgramData\ssh\administrators_authorized_keys`.
6. **Verify.** Open a new connection with key auth only (password and keyboard-interactive
   disabled) and run `echo portkey-ok`.
   - On failure, keep the added line (it is harmless) and list likely causes: StrictModes
     permissions, a custom `AuthorizedKeysFile`, `PubkeyAuthentication no`,
     `AuthenticationMethods publickey,password`.
   - Offer "save as password connection anyway".
7. **Save** the connection as `auth_method=key` with the key, store the host key, record an
   event, and drop the password.

## 9. Module layout

Target: no file over about 400 lines.

### Backend, `portkey/`

| Path | Contents |
|---|---|
| `app.py` | App factory, middleware wiring, static files |
| `config.py` | Paths, port, environment, limits |
| `interfaces.py` | Protocols between packages: `Prompter`, `HostKeyStore`, `KeyProvider`, `ConnectionsRepo`, `GroupsRepo`, `TagsRepo`, settings, events, key vault, connector, session registry |
| `models.py`, `errors.py`, `services.py` | Shared domain records, the HTTP error hierarchy, service wiring |
| `security/` | `hostguard.py`, `auth.py`, `setup_code.py`, `tokens.py`, `redact.py`, `headers.py` |
| `store/` | `db.py` (with migrations), `connections.py`, `groups.py`, `tags.py`, `hostkeys.py`, `settings.py`, `events.py`, `keys_meta.py` |
| `keys/` | `textio.py`, `discovery.py`, `keygen.py`, `vault.py`, `known_hosts.py` |
| `ssh/` | `errors.py`, `client.py` (the asyncssh `SSHClient` subclass that connects auth callbacks to the `Prompter`), `hostkey.py`, `chain.py` (jump chain resolution and connection) |
| `sessions/` | `ringbuffer.py`, `terminal.py`, `registry.py` |
| `wizard/` | `remote_script.py`, `install.py` |
| `api/` | `schemas/` (a package), `deps.py`, `routes_auth.py`, `routes_connections.py`, `routes_groups.py` (folders and tags), `routes_keys.py`, `routes_hosts.py`, `routes_sessions.py`, `routes_settings.py`, `routes_diag.py`, `ws_common.py` (auth handshake and prompt round trip), `ws_terminal.py`, `ws_install.py` |

### Frontend, `portkey/static/`

| Path | Contents |
|---|---|
| `index.html` | Shell |
| `css/` | `base.css` (tokens as CSS custom properties), `layout.css`, `terminal.css`, `dialogs.css`, `prompts.css`, `wizard.css` |
| `js/main.js`, `js/api.js`, `js/store.js`, `js/shell.js`, `js/types.js` | Boot, HTTP client, state, app shell, JSDoc types |
| `js/ws/protocol.js` | WebSocket framing, auth, prompts |
| `js/views/` | `login.js`, `connections.js` (sidebar folder tree, tag chips), `home.js`, `edit-connection.js`, `keys.js`, `suggestions.js`, `wizard.js`, `settings.js`, `sessions.js`, `diagnostics.js` |
| `js/terminal/` | `session.js`, `tabs.js` |
| `js/ui/` | `dialogs.js`, `toast.js`, `dom.js`, `palette.js`, `modal.js`, `tabstrip.js`, `folderpicker.js`, `tagfield.js`, `fpstrip.js` |
| `vendor/xterm/` | xterm, fit addon, search addon |

All colours are CSS custom properties in `base.css`. v1 ships a dark theme only.

### Repo root

`Dockerfile`, `compose.yaml` (for Windows), `compose.dev.yaml`, `Makefile` (wraps
`docker compose run`), `scripts/vendor-xterm.sh` (runs in a container, checks sha256), and
`docs/{api.md, ws-protocol.md, windows-smoke.md}`.

## 10. Tests

Everything runs in containers, driven by `compose.dev.yaml`.

| Service | Purpose |
|---|---|
| `test` | Dev dependencies; runs pytest, ruff and mypy |
| `lint-js` | eslint, plus `tsc --checkJs` on JSDoc types |
| `frontend` | Playwright (the `e2e` image) on `tests/frontend`: the views against a mock API, no network |
| `sshd` | Integration target, host key set A |
| `sshd-rekeyed` | Same users, host key set B |
| `sshd-inner` | Reachable only from `sshd`'s network, for jump tests |
| `app` | The app with a fixture `.ssh` directory set to 0777, holding CRLF and UTF-16 files |
| `evil-origin` | A static page that tries to open the WebSocket |
| `e2e` | Playwright |

### Unit tests

- Text normalisation.
- `known_hosts` parsing: plain, hashed, `[h]:p`, markers, wildcards.
- Key discovery; key generation round trip with a passphrase.
- The host-key decision table, including the typed-name replace.
- Ring buffer offsets.
- The Host / Origin / token matrix.
- 422 redaction.
- The setup code: generation, constant-time comparison, shared backoff, one-shot use, absence
  from every response, event, database file and log record but the startup one, and the reset
  path while the app runs.
- Migrations.
- Jump chain validation: depth limit, cycles, delete-with-dependants.
- Folders: depth limit, sibling-name uniqueness, move-under-descendant rejection, delete
  re-parenting (child folders and connections move up, nothing is deleted).
- Tags: normalisation (trim, `#`, lower-case, pattern), rename-merge, last-use cleanup.
- Bulk edit atomicity: one unknown id or invalid tag changes nothing.
- Routes through the httpx ASGI transport.
- The remote script runs for real under `dash`, `bash` and `busybox sh` against a temporary
  HOME: fresh, existing file, no trailing newline, a second run, and bad permissions.

### Integration tests (sshd image)

The test server is Ubuntu 24.04 with OpenSSH plus `libpam-google-authenticator`, using a fixed
secret; tests compute codes with `pyotp` (in the dev lock; `tests/fixtures/testbed.py` `Totp`).
`sshd` also listens on port 2222 (non-default port tests). The test bed is described in
`tests/fixtures/README.md`; `tests/fixtures/testbed.py` is the single source for servers, users,
passwords, keys and fingerprints.

| User | Setup |
|---|---|
| `pw` | Password only, via `Match User` |
| `keyonly` | `PasswordAuthentication no`, key pre-seeded |
| `fresh` | No `~/.ssh` |
| `tfa` | Keyboard-interactive in two rounds, `Password: ` (pam_unix) then `Verification code: ` (TOTP); `PasswordAuthentication` off; publickey alone allowed |
| `fishy` | Login shell fish |
| `badperms` | `~/.ssh` 0777, so StrictModes makes verification fail |
| `nosh` | `/usr/sbin/nologin` |

Test bed extras: `keyonly` has every fixture key pre-seeded except
`port-key/id_ed25519_portkey` (the "unknown key"); `nosh` signs in but every command exits 1;
each sshd keeps its state for the whole run, so a test that installs a key calls
`reset_ssh_user(...)` afterwards. The fixture `known_hosts` trusts set A for `sshd`,
`[sshd]:2222` and `sshd-inner` (hashed) and deliberately omits `sshd-rekeyed`.

Host key sets A and B are committed as test-only fixtures; `sshd-rekeyed` uses set B. The
changed-key test stores set A's record for `sshd-rekeyed` and expects 4409 when declined and a
replaced record when confirmed by name.

Also covered: an encrypted fixture key with a fake `Prompter`; the wizard's full path and its
idempotency; detach and reattach with `since`; a terminal and a wizard run through `sshd` as a
jump to `sshd-inner`.

### End-to-end (Playwright, Chromium, against `app`)

- First-run password setup (with the setup code read from the app container's log, as the
  owner reads it) and login.
- Adding a connection from a suggestion.
- Opening a terminal, typing `echo hi`, and checking `.xterm-rows`.
- Reload, then reattach and replay.
- The wizard, including the TOTP prompt.
- The host-key confirm dialog and the changed-key dialog.
- Copying the public key (clipboard permission granted).
- `evil-origin` failing to open the WebSocket.
- A wrong Host header getting a rejection.

### Windows-only behaviour

Mounts, ACLs and networking are covered by the manual checklist in `docs/windows-smoke.md`.

## 11. Work packages

| # | Package | Owns | Depends on |
|---|---|---|---|
| 0 | Contracts and skeleton | `pyproject` with the lock, `Dockerfile`, compose files, `Makefile`, `api/schemas.py`, `docs/api.md`, `docs/ws-protocol.md`, `ssh/errors.py`, `interfaces.py`, the DB schema in `store/db.py`, frontend stubs, vendored xterm | - |
| 1 | Security | `security/*`, `routes_auth.py`, `app.py` middleware | 0 |
| 2 | Storage | `store/*` (including `groups.py`, `tags.py`), `routes_connections.py`, `routes_groups.py`, `routes_settings.py` | 0 |
| 3 | Keys | `keys/*`, `routes_keys.py`, `routes_hosts.py` | 0 |
| 4 | SSH core | `ssh/*`, `api/ws_common.py` | 2, 3 (interfaces; may stub) |
| 5 | Terminal | `sessions/*`, `ws_terminal.py`, `routes_sessions.py` | 4 |
| 6 | Wizard | `wizard/*`, `ws_install.py` | 4 |
| 7 | Test infrastructure | `tests/sshd/`, fixtures, `evil-origin`, Playwright harness, `routes_diag.py` | 0 |
| 8a | Frontend shell | `api.js`, `store.js`, and the login, connections (folder tree, tag chips), home (bulk bar), keys, suggestions, settings and diagnostics views | 0 |
| 8b | Frontend terminal | `terminal/*`, `ws/protocol.js`, `ui/dialogs.js`, `ui/palette.js`, `views/wizard.js` (with its folder and tag fields), `views/sessions.js` | 5, 6, 8a |
| 9 | End-to-end hardening | Playwright specs, `docs/windows-smoke.md` | all |

File-by-file ownership, including tests, is in `docs/work-packages.md`.

Order: package 0 first and frozen. Then 1, 2, 3, 7 and 8a in parallel. Then 4. Then 5, 6 and 8b
in parallel. Last, end-to-end hardening. `docs/ux-spec.md` feeds 8a, 8b and all prompt and
dialog copy.
