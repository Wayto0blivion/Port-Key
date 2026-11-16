# Port-Key

A browser-based SSH connection manager for one person. Saved connections appear as a GUI
(nested folders, tags, colour labels); clicking one opens that host in a terminal tab in the
browser. Port-Key prefers key authentication, installs your public key on a host for you (the
`ssh-copy-id` step, 2FA prompts included), and never stores passwords. It runs in a container on
your own Windows PC and listens on that PC only.

What v1 does: terminal sessions as tabs that survive a page reload for 15 minutes, jump hosts,
host key checks (first-seen confirmation, changed keys replaced only by typing the name),
passphrase-protected keys held unlocked in memory, key generation, suggestions from your
`known_hosts`, a quick-connect palette (Ctrl+Shift+K), and a Diagnostics tab.

Design: `docs/PLAN.md`, `docs/architecture.md`, `docs/ux-spec.md`. Wire contracts:
`docs/api.md`, `docs/ws-protocol.md`. First start on the PC: `docs/windows-smoke.md`.

## Run it on Windows (Docker Desktop)

Requirements: Docker Desktop with the WSL2 backend. An SSH key in `%USERPROFILE%\.ssh` helps
(Port-Key can generate one if you have none).

```powershell
git clone https://github.com/Wayto0blivion/Port-Key.git
cd Port-Key
docker compose up -d --build
docker compose logs portkey
```

The log shows a one-time **setup code** in a framed block, such as `Setup code:   K7QM-3XRP-W9TD`.
Open <http://localhost:8822>, type that code in the "Setup code" field (any case, dashes
optional) and choose the app password. The code is printed only to that log, works once, and
changes every time Port-Key starts until a password is set (use the newest one); it stops
another program on the PC from setting the password before you do. Use `localhost` (or
`127.0.0.1`) exactly: Port-Key refuses any other host name, so a web page cannot reach it under
a name of its own.

- `%USERPROFILE%\.ssh` is mounted read-only: Port-Key never changes or deletes your files.
- Keys generated in Port-Key are saved to `%USERPROFILE%\.ssh\port-key` (Docker creates the
  folder on first start), so Windows `ssh -i ~\.ssh\port-key\<name> user@host` (PowerShell) can use them too.
- The database lives in the Docker volume `portkey-data`, never on a Windows folder.
- Another `.ssh` folder: `$env:PORTKEY_SSH_DIR = "D:\keys\.ssh"; docker compose up -d`.
- Host names that only exist in the Windows hosts file may not resolve inside the container.
  Diagnostics > "Check a host" shows what the container sees; add missing names under
  `extra_hosts:` in `compose.yaml`, or save the connection with its IP address.

### Update

```powershell
cd Port-Key
git pull
docker compose up -d --build
```

Saved connections, trusted host keys and settings stay in the `portkey-data` volume. Open
terminal sessions end when the container restarts; open them again from the sidebar.

### Stop

`docker compose down`. Add `-v` only if you want to delete the saved connections, host keys and
settings as well (your key files are never touched).

### Forgot the app password

```powershell
docker compose exec portkey python -m portkey.reset_password --yes
docker compose restart portkey
docker compose logs portkey
```

This clears the app password and signs out every browser; connections, keys, host keys and
settings are kept. The restart makes Port-Key print a fresh setup code (the newest "Setup code"
in the log); open <http://localhost:8822>, type it and set a new password. Until the restart,
nobody can set a password, so a reset never leaves the door open to other programs on the PC.

### Something does not work

Open the Diagnostics tab (Settings > Open Diagnostics): it shows whether the `.ssh` folder is
mounted, which key files could not be used and why, and what a host name resolves to from
inside the container. `docker compose logs portkey` shows the server log; it never contains
passwords, passphrases, tokens or terminal contents (only the setup code, until a password is
set). `docs/windows-smoke.md` lists what to send back.

## Develop and run checks (Linux dev box)

Everything runs in containers; nothing is installed on the host. If the box has no `make`, use
`./mk`, which runs make in a container.

```sh
./mk build                    # build the dev and lint images once
./mk check P=portkey-me       # ruff, mypy and unit tests
./mk lint-js P=portkey-me     # eslint and tsc --checkJs
./mk frontend P=portkey-me    # the views in Chromium against a mock API (no network)
./mk integration P=portkey-me # the SSH core, terminal and wizard against sshd containers
./mk e2e P=portkey-me         # the real app image + sshd containers, driven by Playwright
./mk clean P=portkey-me       # remove that project's containers and volumes
```

`P` is the compose project name; pick your own so parallel runs do not collide. The source is
bind-mounted into the dev containers, so code changes need no rebuild; `./mk e2e` builds the app
image from the tree. In e2e the browser opens `http://localhost:8822` as on the PC (Chromium maps
`localhost` to the app container), and the specs read the setup code from the app container's
log (`tests/e2e/harness/app_log.py`). Screenshots of the real app: see `tests/e2e/screens.py`.
Dependencies are hash-locked: change `pyproject.toml`, then `./mk lock` and `./mk build`.
Notes for whoever works on it next are in `docs/handoff/`.
