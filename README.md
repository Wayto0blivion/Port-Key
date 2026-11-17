# Port-Key

A browser-based SSH connection manager for one person. Saved connections appear as a GUI
(nested folders, tags, colour labels); clicking one opens that host in a terminal tab in the
browser. Port-Key prefers key authentication, installs your public key on a host for you (the
`ssh-copy-id` step, 2FA prompts included), and never stores passwords. It runs in a container on
your own Windows PC and listens on that PC only.

What v1 does: terminal sessions as tabs that survive a page reload for 15 minutes, jump hosts,
host key checks (first-seen confirmation, changed keys replaced only by typing the name),
passphrase-protected keys held unlocked in memory, key generation, suggestions from your
`known_hosts`, a quick-connect palette (Ctrl+Shift+K), and a Diagnostics tab. What changed in
each release, and the known limitations: `CHANGELOG.md`.

Design: `docs/PLAN.md`, `docs/architecture.md`, `docs/ux-spec.md`. Wire contracts:
`docs/api.md`, `docs/ws-protocol.md`. First start on the PC: `docs/windows-smoke.md`.

## Run it on Windows (Docker Desktop)

Requirements: Docker Desktop with the WSL2 backend. An SSH key in `%USERPROFILE%\.ssh` helps
(Port-Key can generate one if you have none); if you have no `.ssh` folder at all, create it
first with `mkdir "$env:USERPROFILE\.ssh"`.

Port-Key is a ready-built image, `ghcr.io/wayto0blivion/port-key`, and one file, `compose.yaml`,
that tells Docker how to run it. Download that file from the latest release into an empty
folder and start it (PowerShell):

```powershell
mkdir Port-Key
cd Port-Key
Invoke-WebRequest https://github.com/Wayto0blivion/Port-Key/releases/latest/download/compose.yaml -OutFile compose.yaml
docker compose up -d
docker compose logs portkey
```

(Or download `compose.yaml` from the release page in the browser and save it in the folder.)
The first `docker compose up -d` downloads the image.

The log shows a one-time **setup code** in a framed block, such as `Setup code:   K7QM-3XRP-W9TD`.
Open <http://localhost:8822>, type that code in the "Setup code" field (any case, dashes
optional) and choose the app password. The code is printed only to that log, works once, and
changes every time Port-Key starts until a password is set (use the newest one); it stops
another program on the PC from setting the password before you do. Use `localhost` (or
`127.0.0.1`) exactly: Port-Key refuses any other host name, so a web page cannot reach it under
a name of its own.

The PowerShell commands below all run in that folder (the one with `compose.yaml`).

- `%USERPROFILE%\.ssh` is mounted read-only: Port-Key never changes or deletes your files.
- Keys generated in Port-Key are saved to `%USERPROFILE%\.ssh\port-key` (Docker creates the
  folder on first start), so Windows `ssh -i ~\.ssh\port-key\<name> user@host` (PowerShell) can use them too.
- The database lives in the Docker volume `portkey_portkey-data` (compose prefixes the
  project name, `portkey`, to the `portkey-data` volume in `compose.yaml`), never on a Windows
  folder. Back it up: see "Back up and restore" below.
- Another `.ssh` folder: create a file named `.env` next to `compose.yaml` with the line
  `PORTKEY_SSH_DIR=D:\keys\.ssh`, then `docker compose up -d`. Compose reads `.env` on every
  run, so the setting survives updates and new PowerShell windows; a `$env:PORTKEY_SSH_DIR`
  set in one window does not, and the next update would mount `%USERPROFILE%\.ssh` again.
- Host names that only exist in the Windows hosts file may not resolve inside the container.
  Diagnostics > "Check a host" shows what the container sees; add missing names under
  `extra_hosts:` in `compose.yaml`, or save the connection with its IP address.

### Update

```powershell
docker compose pull
docker compose up -d
```

`pull` downloads the newest image; `up -d` restarts Port-Key on it (if there was nothing new,
nothing restarts). Saved connections, trusted host keys and settings stay in the
`portkey_portkey-data` volume. Open terminal sessions end when the container restarts; open
them again from the sidebar. The release notes say when a release also changes
`compose.yaml`; only then download it again (and redo any `extra_hosts:` lines you added).

### Stay on one version

By default `compose.yaml` runs the newest release (the image tag `latest`). To stay on one,
add a line to `.env` next to `compose.yaml` (create the file if you have none):

```
PORTKEY_VERSION=0.1.0
```

then `docker compose pull` and `docker compose up -d`. `PORTKEY_VERSION=0.1` follows the
bug-fix releases of 0.1 only. Delete the line to go back to the newest. Back up before you
move to an older version: once a newer Port-Key has upgraded the database, an older one refuses
to start on it (restore the backup you made before the update instead).

### Back up and restore

Everything Port-Key knows (connections, folders, tags, trusted host keys, settings, the app
password's hash) is one SQLite database in the `portkey_portkey-data` volume. Your key files are
not in it; they stay in `.ssh`. A Docker Desktop reset, a WSL reset or `docker compose down -v`
deletes the volume, so keep a backup. Stop Port-Key first: SQLite keeps recent changes in a
side file while it runs.

Back up to `portkey-data.tgz` in the current folder:

```powershell
docker compose stop portkey
docker compose run --rm --no-deps --entrypoint tar -v "${PWD}:/backup" portkey czf /backup/portkey-data.tgz -C /data .
docker compose start portkey
```

Restore it (this replaces what the volume holds now; it also works on a new PC before the
first start):

```powershell
docker compose stop portkey
docker compose run --rm --no-deps --entrypoint sh -v "${PWD}:/backup" portkey -c "rm -f /data/portkey.db /data/portkey.db-wal /data/portkey.db-shm && tar xzf /backup/portkey-data.tgz -C /data"
docker compose up -d
```

Both run a one-off container of the `portkey` service, with the same image and volume, so
nothing else is downloaded. After a restore, browsers that were signed in stay signed in only
if their token is in the backup; otherwise sign in again.

### Stop

`docker compose down`. Add `-v` only if you want to delete the saved connections, host keys and
settings as well (your key files are never touched); back up first if you may want them back.

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
set). To report a problem, open an issue on GitHub with what the "What to send back" section
of `docs/windows-smoke.md` lists (never a private key or the contents of your `.ssh` folder).

## Build from source

Instead of downloading the image you can build it from this repository; you need Git for
Windows as well. `compose.build.yaml` adds the build to `compose.yaml` and tags the result
`portkey:local`, so it is never mistaken for a downloaded release:

```powershell
git clone https://github.com/Wayto0blivion/Port-Key.git
cd Port-Key
docker compose -f compose.yaml -f compose.build.yaml up -d --build
docker compose logs portkey
```

To make the other PowerShell commands in this README work unchanged in the clone (backup and restore
included, which otherwise would download the release image), create a `.env` file in the
`Port-Key` folder with these two lines (git ignores `.env`):

```
COMPOSE_PATH_SEPARATOR=;
COMPOSE_FILE=compose.yaml;compose.build.yaml
```

Update with `git pull`, then `docker compose up -d --build` (with that `.env`; otherwise give
both `-f` options again). `docker compose pull` does not apply here, and `PORTKEY_VERSION` has
no effect: check out a tag instead (`git checkout v0.1.0`). The clone and a downloaded
`compose.yaml` share the project name `portkey` and the `portkey_portkey-data` volume, so moving
from one to the other keeps your data; run only one of them at a time.

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
image from the tree (`portkey:local`, as `compose.build.yaml` does). In e2e the browser opens
`http://localhost:8822` as on the PC (Chromium maps `localhost` to the app container), and the
specs read the setup code from the app container's log (`tests/e2e/harness/app_log.py`).
Screenshots of the real app: see `tests/e2e/screens.py`. Dependencies are hash-locked: change
`pyproject.toml`, then `./mk lock` and `./mk build`. Narrower runs, conventions, what to run
before a pull request and how a release is cut: `CONTRIBUTING.md`.

## Licence

Port-Key is released under the MIT licence; see `LICENSE`. The vendored xterm.js files in
`portkey/static/vendor/xterm/` carry their own MIT licence, in
`portkey/static/vendor/xterm/LICENSE.xterm`.
