# Port-Key plan

Status: agreed with the owner on 2026-11-15. Detail lives in `docs/architecture.md` and
`docs/ux-spec.md`; where they disagree with this file, this file wins.

The design mockups
are in `docs/design/` as static HTML.

## What it is

A browser-based SSH connection manager for one person. Saved connections are shown as a GUI;
clicking one opens that host in an in-browser terminal (xterm.js). Key authentication is
preferred; passwords are a fallback and are never stored.

## Decided

- Runs on the owner's Windows PC in a container (Docker Desktop, WSL2 backend). The browser is
  on the same PC. The PC's private key is used directly and never leaves the PC.
- v1 features: saved connections organised in nested folders and tags, in-browser terminal with
  several sessions as tabs, show/copy public key, key install wizard (ssh-copy-id equivalent),
  host suggestions from known_hosts, key generation in the app, jump hosts.
- The app requires a login: one app password set on first visit, remembered for 30 days.
- The owner's key is an ordinary key file in `.ssh` with no passphrase today. Passphrase
  support is built anyway because they may add one.
- Not in v1: import from `~/.ssh/config`, SFTP, port forwarding, split panes, snippets,
  agent-only keys, PuTTY `.ppk` keys, deleting key files, connect-once without saving,
  light theme (colours are CSS custom properties so it is cheap later).
- Development and every check (tests, lint, types) run in containers on the Linux dev box.

## Stack

| Layer | Choice |
|---|---|
| Runtime | `python:3.12-slim`, non-root, read-only root filesystem |
| Web | FastAPI + uvicorn |
| SSH | asyncssh (async, keepalive, key generation, hashed known_hosts, kbd-interactive) |
| Storage | SQLite (stdlib, WAL) in a named volume |
| Frontend | Vanilla JS as native ES modules, no build step, vendored xterm.js 5.5 + fit addon |
| Tests | pytest, sshd test containers, Playwright |

No file over about 400 lines.

## Mounts and ports (Windows)

- `%USERPROFILE%\.ssh` -> `/ssh`, read-only. Port-Key never edits or deletes the owner's files.
- `%USERPROFILE%\.ssh\port-key` -> `/keys-generated`, read-write, create-only. Generated keys
  land here so Windows `ssh` can use them too.
- Named volume `portkey-data` -> `/data` for SQLite. Never put SQLite on a Windows bind mount.
- Published on `127.0.0.1:8822` only.

## Security (built in from the first commit)

- Host header allowlist (blocks DNS rebinding) and exact Origin check on every WebSocket and
  every non-GET request (blocks cross-site WebSocket hijacking).
- No cookies. Bearer token in `localStorage`; WebSockets authenticate in their first message.
  Tokens never appear in URLs.
- Host keys: entries in the owner's `known_hosts` are trusted. A first-seen key needs an
  explicit fingerprint confirmation. A changed key stops the connection before any credential
  is sent; replacing it requires typing the host's name.
- Passwords and passphrases travel only inside WebSocket prompt replies, are held in memory
  only, and are never logged. Terminal input and output are never logged.
- Strict CSP; no clipboard (OSC 52) addon; container drops all capabilities.

## Terminal protocol (`/ws/terminal`)

- Client: `auth`, then `open {connection_id, cols, rows}` or `attach {session_id, since}`;
  binary frames are input; `resize`, `ping`, `close`.
- Server: `status`, `prompt` (host key, passphrase, password, keyboard-interactive), `ready`,
  binary output frames, `exit`, `error` with a close code (4409 = host key changed).
- SSH keepalive every 30 s. A session survives a dropped WebSocket or a page reload for
  15 minutes and replays from a 256 KB ring buffer. A dead SSH connection is never reopened
  automatically; the tab shows Disconnected with a Reconnect button.
- One SSH connection per session in v1 (no connection pooling).

## Key install wizard

1. Read the host key; confirm it if unseen; stop if changed.
2. Try the key first. If it already works, save and finish.
3. Sign in with the password (2FA prompts are relayed).
4. Run a POSIX `sh` script over stdin that creates `~/.ssh` (700) and `authorized_keys` (600)
   if missing, appends the key only if it is not already there, and reports loose permissions.
5. Reconnect with the key only to prove it works, then save the connection and drop the
   password.

Named failure states: unreachable, refused, name not found, wrong password, password login
disabled (show the manual command), key added but still refused (permissions), read-only
`authorized_keys`, non-POSIX remote.

## Work packages

| # | Package | Depends on |
|---|---|---|
| 0 | Contracts and skeleton: Dockerfile, compose files, API schemas, WS protocol doc, DB schema, error codes, vendored xterm | - |
| 1 | Security: host/origin guard, auth, tokens, headers, log redaction | 0 |
| 2 | Storage: connections, folders, tags, host keys, settings | 0 |
| 3 | Keys: discovery, text normalisation (CRLF, BOM, UTF-16), generation, vault, known_hosts | 0 |
| 7 | Test infrastructure: sshd containers, fixtures, evil-origin page, Playwright harness | 0 |
| 8a | Frontend shell: connections, keys, suggestions, settings views | 0 |
| 4 | SSH core: client, host key flow, prompt round trip | 2, 3 |
| 5 | Terminal: sessions, ring buffer, `/ws/terminal` | 4 |
| 6 | Wizard: remote script, `/ws/install` | 4 |
| 8b | Frontend terminal, tabs, dialogs, wizard | 5, 6, 8a |
| 9 | End-to-end hardening and the Windows smoke checklist | all |

Package 0 is frozen before the others fan out.

## Things only the Windows PC can prove

Docker Desktop behaviour is inferred, not verified: whether the `.ssh\port-key` folder is
created with usable ACLs, whether VPN and Tailscale hosts and Windows `hosts`-file names are
reachable from the container. `docs/windows-smoke.md` will list these checks, and a diagnostics
page will show what the container can resolve and reach.

## Jump hosts

A connection may name another saved connection as its jump (`jump_id`). Chains are at most
three hops and cycles are rejected on save. Each hop signs in with its own saved settings and
gets its own host key check; prompts say which hop they belong to. A connection that others
jump through cannot be deleted until they are re-pointed. The wizard can install a key on a
host that is reached through a jump.

## Folders and tags (added 2026-11-16)

The owner expects a lot of connections, so the single group per connection is replaced by:

- **Folders**: a tree at most 3 levels deep. Sibling names are unique. Deleting a folder moves
  its contents up a level; it never deletes connections. Collapsed state is per browser.
- **Tags**: lower-case labels, many per connection, created on first use and gone when unused.
  Tags have no colour; the colour label stays the per-connection safety signal.
- The sidebar shows the folder tree (Pinned on top, Ungrouped at the bottom) with a row of tag
  chips under the filter; Home's table gets Folder and Tags columns and a bulk bar (move to
  folder, add or remove tags). Filtering and search stay in the browser.

## Owner's answers (2026-11-15)

1. App login: yes, a password.
2. Generated keys: `.ssh\port-key` on the PC.
3. Jump hosts: build the capability into v1.
4. PC key: ordinary key pair in `.ssh`, no passphrase; may add one later.
