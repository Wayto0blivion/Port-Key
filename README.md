# Port-Key

A browser-based SSH connection manager for one person. Saved connections appear as a GUI
(folders, tags, colour labels); clicking one opens that host in a terminal in the browser.
Port-Key prefers key authentication, installs your public key on a host for you (the
`ssh-copy-id` step), and never stores passwords. It runs in a container on your own Windows PC
and listens on that PC only.

Status: under construction (v1). Design: `docs/PLAN.md`, `docs/architecture.md`,
`docs/ux-spec.md`; wire contracts: `docs/api.md`, `docs/ws-protocol.md`.

## Run it on Windows (Docker Desktop)

Requirements: Docker Desktop with the WSL2 backend, and an SSH key in `%USERPROFILE%\.ssh`
(Port-Key can generate one if you have none).

```powershell
git clone https://github.com/Wayto0blivion/Port-Key.git
cd Port-Key
docker compose up -d
```

Open <http://localhost:8822> and set the app password on first visit.

- `%USERPROFILE%\.ssh` is mounted read-only: Port-Key never changes or deletes your files.
- Keys generated in Port-Key are saved to `%USERPROFILE%\.ssh\port-key`, so Windows `ssh` can
  use them too.
- The database lives in the Docker volume `portkey-data`.
- Another `.ssh` folder: `$env:PORTKEY_SSH_DIR = "D:\keys\.ssh"; docker compose up -d`.
- Host names that only exist in the Windows hosts file may not resolve inside the container;
  add them under `extra_hosts:` in `compose.yaml` (Diagnostics shows what resolves).

Update: `git pull` then `docker compose up -d --build`. Stop: `docker compose down` (add
`-v` only if you want to delete the saved connections).

## Develop and run checks (Linux dev box)

Everything runs in containers; nothing is installed on the host. If the box has no `make`, use
`./mk`, which runs make in a container.

```sh
./mk build                 # build the dev and lint images once
./mk check P=portkey-me    # ruff, mypy and unit tests
./mk lint-js P=portkey-me  # eslint and tsc --checkJs
./mk integration P=portkey-me
./mk e2e P=portkey-me
```

`P` is the compose project name; pick your own so parallel runs do not collide. The source is
bind-mounted into the dev containers, so code changes need no rebuild. Dependencies are
hash-locked: change `pyproject.toml`, then `./mk lock` and `./mk build`. File ownership for
the parallel work packages is in `docs/work-packages.md`.
