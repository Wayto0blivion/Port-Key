# Windows smoke checklist

Everything Port-Key does was tested on the Linux dev box, in containers, against test SSH
servers. What only your Windows PC can prove is listed here: Docker Desktop's mounts and
permissions, the PC's networks (LAN, VPN or Tailscale, the Windows `hosts` file), Windows
OpenSSH using a key that Port-Key made, and the look and keys in Edge or Chrome on Windows.

Go through it once after the first install, and again after an update that touches mounts or
networking. Tick each box; when one fails, stop and send what "What to send back" lists. Allow
about 30 minutes.

Commands are for PowerShell, run in the `Port-Key` folder that holds `compose.yaml`. `<you>` is
your Windows user name, `<host>` and `<user>` a server of yours.

## Verified so far

On 2026-11-17 the owner went through this on the Windows PC (Docker Desktop, WSL2) and
confirmed: the first start (step 1), the `.ssh` folder mounted and read (step 3), the
`port-key` folder created and writable (steps 1 and 3), a suggestion from `known_hosts`
(step 3), and a terminal session (step 4). Not tested yet: hosts reached over
Tailscale or another VPN (step 5). That run used an image built from the source tree; the
download path below (the released image) is what to check next.

## 0. Before you start

- [ ] Docker Desktop is running with the WSL2 backend (Settings > General > "Use the WSL 2 based
      engine" is ticked).
- [ ] `docker version` answers for both Client and Server.
- [ ] You have an SSH key in `C:\Users\<you>\.ssh` that already works from Windows
      (`ssh <user>@<host>` signs in without a password). If you have no key, skip the
      key-specific checks in step 3 and use step 7 to make one.

## 1. First start

In an empty `Port-Key` folder, download `compose.yaml` from the latest release and start it
(README.md, "Run it on Windows"):

```powershell
Invoke-WebRequest https://github.com/Wayto0blivion/Port-Key/releases/latest/download/compose.yaml -OutFile compose.yaml
docker compose up -d
docker compose ps
```

(Built from source instead: `docker compose -f compose.yaml -f compose.build.yaml up -d
--build` in the clone; README.md, "Build from source".)

- [ ] `docker compose up -d` downloads `ghcr.io/wayto0blivion/port-key` without asking you to
      sign in (`docker login` is not needed).
- [ ] `docker compose ps` shows `portkey` as `running (healthy)` within a minute.
- [ ] `docker image inspect ghcr.io/wayto0blivion/port-key:latest --format '{{json .Config.Labels}}'`
      shows `"org.opencontainers.image.version":"0.1.0"` (the release's version).
- [ ] `docker compose logs portkey` shows `Uvicorn running on http://0.0.0.0:8000` (the port
      inside the container; Docker publishes it as 8822) and no `Traceback`.
- [ ] The same log shows one block between two lines of `=` with `Setup code:` and a code like
      `K7QM-3XRP-W9TD`, saying to open http://localhost:8822 and type it in the "Setup code"
      field. Keep it for step 2.
- [ ] Only this PC can reach it: `netstat -ano | findstr :8822` shows `127.0.0.1:8822` and no
      `0.0.0.0:8822` line.
- [ ] `C:\Users\<you>\.ssh\port-key` exists now (Docker creates it).

## 2. Where to find the app

- [ ] <http://localhost:8822> in Edge (and Chrome, if you use it) shows "Set a password for
      Port-Key" with a "Setup code" field first; its help text names
      `docker compose logs portkey`.
- [ ] Type a wrong code (for example `AAAA-BBBB-CCCC`) with a password: "That setup code is not
      correct…" appears under the code field and the password fields stay filled.
- [ ] Type the code from step 1 in lower case without the dashes, and set the password: it
      works.
- [ ] `docker compose restart`, then `docker compose logs portkey`: no new `Setup code:` line
      appears (a password exists now).
- [ ] Then: "No connections yet" names your key (`id_ed25519` or similar) and the Windows path
      `C:\Users\<you>\.ssh`, never `/ssh`.
- [ ] <http://127.0.0.1:8822> works too (it asks you to sign in: a different address is a
      different browser storage).
- [ ] `http://<your PC name>:8822` does not work (refused or no page). That is on purpose.
- [ ] Close the browser tab and open <http://localhost:8822> again: you are still signed in.

## 3. The `.ssh` folder is read, and the `port-key` folder is usable

Open Settings > Open Diagnostics.

- [ ] ".ssh folder" shows your Windows path with "found", and "Keys found" is the number of key
      pairs you have.
- [ ] "known_hosts" shows "found" if you have `C:\Users\<you>\.ssh\known_hosts`.
- [ ] "Generated keys" shows `C:\Users\<you>\.ssh\port-key` with "writable".
- [ ] "Keys that could not be used" lists only files you expect (PuTTY `.ppk`, files that are
      not keys). A key you use every day must not be there.
- [ ] Keys tab: your keys are listed with Windows paths; the Default pill is on the key you
      expect. "Copy public key" puts the same line into the clipboard as
      `Get-Content ~\.ssh\id_ed25519.pub` shows (paste it into Notepad to compare).
- [ ] Home > "Suggested from known_hosts" lists hosts you have connected to before.
- [ ] Port-Key changed nothing: the modification times in `C:\Users\<you>\.ssh` are the same as
      before step 1 (`Get-ChildItem ~\.ssh | Select Name, LastWriteTime`).

## 4. A LAN host

- [ ] Add connection: Host = a LAN server's IP address or name, User = your user. Next.
- [ ] If your key is already on it: "Your key is already installed" and Save connection.
      Otherwise sign in once with its password and see the four green lines.
- [ ] Open terminal: a shell appears; `uname -a` prints; the tab dot is green and the frame has
      the colour you picked.
- [ ] Home shows a filled green dot for it (port reachable).
- [ ] Reload the page (F5): the tab comes back with the same shell and its recent output.
- [ ] Close the tab with the shell still open: the browser warns before leaving
      (Settings > "Warn before leaving" is on).

## 5. A VPN or Tailscale host

Connect your VPN (or Tailscale) on Windows first.

- [ ] Diagnostics > Check a host with the host's VPN address (Tailscale: `100.x.y.z`): it
      "answered in N ms".
- [ ] The same with its MagicDNS or VPN DNS name. Write down whether it resolves; if not, use
      the address (or `extra_hosts`, step 6).
- [ ] Add it as a connection and open a terminal: a shell appears.
- [ ] Disconnect the VPN while the terminal is open, wait 2 minutes: the tab says the
      connection was lost. Reconnect the VPN and press Enter in the tab: a new shell opens.

## 6. A name from the Windows `hosts` file

If you have none, add one (as administrator) to `C:\Windows\System32\drivers\etc\hosts`:
`192.168.x.y  lab-box` with the address of a LAN server.

- [ ] Diagnostics > Check a host `lab-box`. Write down the result: resolved, or "Name or service
      not known". (Docker Desktop may or may not pass the hosts file to containers.)
- [ ] If it did not resolve: in `compose.yaml` uncomment `extra_hosts:` and add
      `- "lab-box:192.168.x.y"`, run `docker compose up -d`, and check again: it resolves.
- [ ] A connection with Host `lab-box` opens a terminal.

## 7. A key generated in Port-Key, used from Windows ssh

- [ ] Keys > Generate key: Ed25519, name `id_ed25519_portkey`, no passphrase. The detail pane
      shows `C:\Users\<you>\.ssh\port-key\id_ed25519_portkey`, and the file is there in
      Explorer.
- [ ] "Install on a host…": install it on a test server with the wizard (password once).
- [ ] From PowerShell: `ssh -i ~\.ssh\port-key\id_ed25519_portkey <user>@<host>` signs in
      without a password. If Windows ssh says `UNPROTECTED PRIVATE KEY FILE` or `bad
      permissions`, write down `icacls ~\.ssh\port-key\id_ed25519_portkey` and send it; the
      workaround is `icacls ~\.ssh\port-key\id_ed25519_portkey /inheritance:r /grant:r
      "$($env:USERNAME):(R)"`.
- [ ] Generate a second key with a passphrase. Connecting with it asks "Unlock …"; the top bar
      shows "1 key unlocked" and Lock; after Lock, the next connect asks again.
- [ ] `ssh -i ~\.ssh\port-key\<that key> <user>@<host>` asks for the same passphrase and works.

## 8. The browser on Windows

- [ ] Fonts: the app text is Segoe UI, the terminal Cascadia Mono (or another monospace).
      Nothing overlaps or is cut off at your usual window size, and at 150 % scaling.
- [ ] Ctrl+Shift+K opens the quick-connect palette. If Edge opens a duplicate tab instead,
      Ctrl+Shift+Space must open it.
- [ ] In a terminal: select text and press Ctrl+C (copies, no `^C`); without a selection
      Ctrl+C interrupts (`sleep 100` stops). Ctrl+V pastes. Right-click pastes (the browser may
      ask once to allow the clipboard). Pasting several lines asks "Paste N lines…" first.
- [ ] Alt+1 and Alt+0 switch tabs; Ctrl+Shift+F finds text in the scrollback.

## 9. Restart and update

- [ ] `docker compose restart`: you stay signed in; connections and trusted host keys are kept.
- [ ] `docker compose pull`, then `docker compose up -d`: it says the image is up to date (or
      downloads a newer one and recreates `portkey`); you stay signed in and everything is
      kept. Settings > About shows the version you expect.
- [ ] Settings > Change password works, and the old password no longer signs in.
- [ ] Optional: `docker compose exec portkey python -m portkey.reset_password --yes`, then
      reload the page: it asks to set a password again and says Port-Key needs a restart for a
      setup code; setting one is refused. `docker compose restart portkey` and
      `docker compose logs portkey` show a new code; with it the password can be set, and the
      connections are still there.

## What to send back

When a check fails, send:

1. The step and the box that failed, what you did and what you saw (a screenshot is best).
2. A screenshot of the Diagnostics tab, and of the "Check a host" result if a host was
   involved.
3. `docker compose ps` and `docker compose logs --tail 200 portkey`. The log never contains
   passwords, passphrases, tokens or terminal contents, but it does contain host names, user
   names and key fingerprints; edit those out if you prefer. Until a password is set it also
   shows the setup code; a code is useless once the password is set.
4. `docker version` and `wsl --version`.
5. Browser problems: the browser name and version, and the Console tab of the developer tools
   (F12) after reproducing it.

Never send the contents of your `.ssh` folder, a private key, or the app password.
