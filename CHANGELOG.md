# Changelog

What changed in each Port-Key release. Install and update instructions are in `README.md`.

## [0.1.0] - 2026-11-17

The first release. Port-Key is a browser-based SSH connection manager for one person: it runs
in a container on your own Windows PC (Docker Desktop), only that PC can reach it, and you use
it at <http://localhost:8822>.

Installing is one download: get `compose.yaml` from this release, put it in an empty
folder and run `docker compose up -d`. The image comes ready-built from
`ghcr.io/wayto0blivion/port-key` for 64-bit Intel/AMD and ARM machines.

### What it does

- Keeps your saved SSH connections in a sidebar with nested folders, tags and colour labels.
  Click one to open that host in a terminal tab in the browser.
- Terminal tabs survive a page reload: reopen the page within 15 minutes and the session is
  still there.
- Prefers keys over passwords. It uses the keys already in your `.ssh` folder (read-only: it
  never changes or deletes your files), can generate new ones into `.ssh\port-key`, and puts
  your public key on a server for you (the `ssh-copy-id` step), including servers that ask for
  a 2FA code. It never stores a password.
- Keys protected by a passphrase are unlocked once and kept unlocked in memory until you lock
  them or Port-Key restarts.
- Jump hosts: reach a server through another one.
- Checks host keys: asks you to confirm a server the first time, and replaces a changed key
  only after you type the server's name.
- Suggests servers from your `known_hosts` file.
- A quick-connect palette (Ctrl+Shift+K) and a Diagnostics tab that shows what Port-Key can
  see: your `.ssh` folder, keys it could not use and why, and what a host name resolves to.
- Protected by an app password. The first visit needs a one-time setup code from
  `docker compose logs portkey`, so no other program on the PC can claim Port-Key first.

### Known limitations

- Not yet tested with servers reached over Tailscale or another VPN. They should work like any
  other network, but if one does not, the Diagnostics tab's "Check a host" shows what the
  container sees; please report what it says.
- After a page reload, a full-screen program (such as `top`, `htop`, `vim` or `less`) shows
  only part of its screen until it redraws. Most of them redraw on Ctrl+L, or on their next
  update.
- Another program running on the same PC can lock you out of signing in, for up to 10 minutes
  at a time, by guessing wrong passwords: Port-Key sees every program on the PC as the same
  visitor, so the delay that slows down guessing applies to you too. It cannot get in this
  way; wait and sign in again.
- Keys that live only in an SSH agent (such as the Windows OpenSSH agent or Pageant) are not
  used, and PuTTY `.ppk` key files are not read. Use an OpenSSH key file, or let Port-Key
  generate one.
