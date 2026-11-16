# Port-Key UX specification (v1)

The visual reference is the set of mockups in `docs/design/*.dc.html`. They are static HTML
mockups: copy colours, spacing and copy from them, and ignore their `<x-dc>` / `sc-for`
templating. Where a mockup and this document disagree on behaviour, this document wins. The
backend contracts are in `docs/architecture.md`.

Port-Key is used by one person, an IT admin and developer who manages a few dozen Linux servers
and appliances on office LANs and a VPN. It runs on their own Windows PC and is used in a
desktop browser on a large monitor. A phone layout is not a priority, but nothing may break in
a narrow window.

All example hosts in this document are fictional.

| Name | Address | Colour | Notes |
|---|---|---|---|
| web-prod | `john@192.168.10.42` | red | Production |
| build-box | `john@192.168.10.20` | blue | |
| backup-nas | `admin@192.168.10.60` | purple | |
| print-srv | `root@192.168.10.15` | grey | Password only |
| imaging-01 | `admin@192.168.10.31:2222` | amber | |
| parser-vm | `root@192.168.10.27` | amber | |
| vpn-gw | `john@10.8.0.1` | green | |

The PC is `WORKPC` and the Windows user is `john`.

## 1. Information architecture

### The shell

Three parts: a top bar, a resizable left sidebar (default 280px, 200 to 420px) and a main pane
with a tab strip.

**Top bar**

- Left: the wordmark "Port-Key". The hyphen is drawn as a small brass key glyph.
- Centre: a quick-connect field, "Connect to…  Ctrl+Shift+K". Clicking it opens the palette.
- Right:
  - an unlocked-keys indicator, shown only while a passphrase is held: "1 key unlocked" with a
    Lock button;
  - Keys and Settings buttons.

**Sidebar: connections only.** From top to bottom:

- A filter field. Besides plain text (name, host, user, notes, folder path) it understands
  `#tag` tokens.
- A row of tag chips under the filter: most used first, then "More…". Selecting chips filters
  to connections that have all selected tags. Tags have no colour.
- The "Pinned" section (virtual, always first).
- The folder tree: collapsible folders, 12px indent per level, at most 3 levels, each with a
  count that includes its descendants. Collapsed state is remembered per browser.
- "Ungrouped" (always last of the tree).
- "Suggested from known_hosts (14)", collapsed by default.
- A footer button, "Add connection".

While a filter or tag chip is active, folders with no matching connections are hidden.

**Folder actions.** Drag a connection row onto a folder to move it there. A folder's context
menu offers New folder, Rename, Move and Delete. Deleting a folder moves its folders and
connections up one level (to Ungrouped at the top level); it never deletes connections, and it
shows a 10-second Undo toast.

**Main pane tab strip**

- The Home tab is pinned on the far left and shows only a house icon.
- Keys, Settings and Diagnostics open as closable tabs, not as separate routes.
- Each terminal session is its own tab.

### Why this shape

- **Sessions are never unmounted.** Keys, Settings and Home are tabs beside the terminals, so
  going back to the list never kills or hides a shell. The list is always visible in the
  sidebar anyway.
- **Dense two-line rows in the sidebar, not cards.** A few dozen hosts fit on one screen at
  about 44px per row.
- **Cards appear only on Home,** for Recent, where recognition matters more than density.
- **Folders plus tags plus a colour label.** The owner has a lot of connections. Folders give
  each connection one place ("Office LAN / Servers", "VPN", "Appliances"); tags cut across
  folders (`prod`, `web`, `dell`); the colour label stays a per-connection safety signal and
  is separate from tags.
- **Pinned connections** stay at the top. Recency is used for Home and for palette ranking.
- **Multiple sessions are tabs.** There are no split panes.

### Sidebar row

Each row shows:

- a 3px colour-label stripe on the left edge;
- a reachability dot;
- the name in semibold;
- a monospace subline, `john@192.168.10.42`, with the port only if it is not 22
  (`admin@192.168.10.31:2222`);
- on the right: an open-sessions badge ("2") and an auth glyph, a brass key or a grey "pw" for
  password-only connections.

Rows stay 44px high and do not show tag chips.

### Session tabs

- Each tab shows a colour stripe on its top edge, a status dot, the name and a close ×.
- A second session to the same host is titled "web-prod · 2".
- Middle-click closes a tab. Drag reorders tabs.
- Background tabs show an activity dot when new output arrives.

### After a browser reload

Sessions survive a dropped connection or a page reload. The backend keeps each shell alive for
15 minutes and the tab reattaches and replays recent output (up to 256 KB). Tabs whose session
has gone come back in the Disconnected state.

### Narrow window

- Below 900px, the sidebar becomes an overlay drawer opened with a menu button in the top bar,
  and the tab strip scrolls horizontally.
- Below 600px, dialogs go full width and the quick-connect field shrinks to an icon.

## 2. Screens and states

### Set password (first visit) and sign in

- First visit: title "Set a password for Port-Key". Body: "Port-Key can open shells on your
  servers, so it asks for a password even though it only listens on this PC." Fields: Setup
  code, Password, Confirm password. Button: [Set password]. Visual reference:
  `docs/design/SetPassword.dc.html`.
- Setup code: the first field, focused on arrival, monospace, placeholder `XXXX-XXXX-XXXX`,
  shown upper-case; any case and any separators are accepted. Helper text: "Port-Key printed
  this code when it started. To see it, run `docker compose logs portkey` on this PC." (`portkey`
  is the service name in `compose.yaml`.) Empty: "Enter the setup code. docker compose logs
  portkey shows it." Wrong code, under the field, keeping both password fields filled: "That
  setup code is not correct. Use the newest code in the log (docker compose logs portkey)."
  Wrong codes count towards the same limit as wrong passwords: "Too many attempts. Try again in
  N seconds." in the form, with the button disabled until the countdown ends.
- When Port-Key holds no code (its password was cleared while it ran), the form says, before
  and after a submit: "Port-Key has no setup code right now: its password was cleared while it
  was running. Restart it on this PC (docker compose restart portkey), then use the code that
  docker compose logs portkey shows."
- What the owner sees in `docker compose logs portkey` while no password is set: one block
  between two rules of `=`, with "Setup code:   K7QM-3XRP-W9TD", the address to open
  (`http://localhost:8822`) and where to type the code. Port-Key prints a new code at every
  start until a password is set; only the newest works. Once a password is set it prints none.
- Later visits, when the saved sign-in has expired: title "Sign in to Port-Key", one Password
  field, button [Sign in]. Wrong password: "That password is not correct. Try again." After
  repeated failures: "Too many attempts. Try again in 30 seconds."
- A sign-in lasts 30 days and renews itself while Port-Key is in use.

### First run, key found

Main pane, centred, about 480px wide:

- Title: "No connections yet".
- Body: "Port-Key found your key **id_ed25519** in `C:\Users\john\.ssh`. Add a host and
  Port-Key installs this key on it, so you stop typing that host's password."
- The key's fingerprint with its colour strip: `ED25519 SHA256:Xk3fP0…r9Qa`.
- Buttons: [Add connection] (primary) and [Copy public key].
- Below: "Or start from a host this PC has connected to before", followed by 3 suggestion rows
  (`web-prod · 192.168.10.42`, `imaging-01 · 192.168.10.31:2222`, `parser-vm · 192.168.10.27`),
  each with [Add], then "See all 14".

Always show the Windows path, not the container path.

### First run, no key found

- Title: "No SSH key found".
- Body: "Port-Key looked in `C:\Users\john\.ssh` and found no key pair. Generate one here to
  log in to your servers without passwords."
- Buttons: [Generate key] (primary) and [Add connection with a password].
- Helper: "Have a key somewhere else? Copy it into `C:\Users\john\.ssh`, then select Check
  again." with a [Check again] link.
- If the only keys found are unsupported: "Port-Key found a PuTTY key (`.ppk`), which it can't
  use. Generate a key here and install it with the wizard."

### Home tab

**Recent:** up to 6 cards, each showing:

- colour stripe and name;
- `john@192.168.10.42`;
- "Last connected 14:02 today";
- a reachability dot;
- a hover [Connect].

**All connections:** a sortable table.

| Column | Example |
|---|---|
| Checkbox | selects the row for bulk actions |
| Name | web-prod |
| Address (monospace) | `john@192.168.10.42` |
| Folder | `Office LAN / Servers` (the full path) |
| Tags | chips: `prod` `web` |
| Signs in with | `id_ed25519`, or "Password" |
| Last connected | Today 14:02 |
| Notes (truncated) | Production. Red on purpose. |
| Row menu "⋯" | |

Example rows:

- `parser-vm`, `root@192.168.10.27`, Appliances
- `imaging-01`, `admin@192.168.10.31:2222`, Appliances
- `web-prod`, `john@192.168.10.42`, Office LAN / Servers, `prod` `web`
- `vpn-gw`, `john@10.8.0.1`, VPN

**Bulk bar.** When any rows are checked, a bar above the table shows "3 selected" and
[Move to folder…], [Add tag…], [Remove tag…]. Each applies to every selected connection at
once (all or nothing) and shows an Undo toast.

**Suggestions panel:** shown only if there are undismissed suggestions.

**Reachability dot**

- A TCP check on the SSH port while Home is visible, every 60 seconds.
- Reachable is a filled green dot; not checked is a grey ring; unreachable is a red ring. The
  states differ in shape as well as colour.
- Tooltips: "Port 22 reachable, 3 ms" or "No response on port 22".
- Connections that use a jump host are not checked: grey dot, tooltip "Checked when you
  connect, reached via build-box".

### Terminal view

A thin session bar sits above the terminal and shows:

- the colour chip;
- `john@web-prod` and `192.168.10.42:22` in monospace;
- `via build-box` when the connection uses a jump host;
- the key in brass, "id_ed25519";
- "Connected 12 min";
- actions: [Find], [Copy ssh command], [A−] [A+], [Disconnect].

A 2px frame in the connection's colour runs around the terminal. The terminal is always dark.

**Session states**

- **Connecting.** A centred overlay on the dimmed terminal with a live checklist and a [Cancel]
  button. The tab dot pulses amber.
  - "Reaching 192.168.10.42:22…"
  - "Checking host key ✓"
  - "Signing in with id_ed25519…"
  - With a jump host, the hop comes first: "Reaching build-box… ✓", then the target's lines.
- **Connected.** The tab dot is green and focus is in the terminal.
- **Reattaching.** After a reload or a dropped connection: "Reattaching to web-prod…", then the
  replayed output. No user action is needed.
- **Disconnected.** The scrollback stays. A bar at the bottom of the terminal reads one of:
  - "Disconnected from web-prod at 14:32. The remote host closed the connection."
  - "Connection to web-prod lost at 14:32. No response for 90 seconds."

  The bar continues "Press Enter or select Reconnect to open a new shell." and has [Reconnect]
  and [Close tab]. The tab dot turns grey and the tab text is muted. Port-Key never reconnects
  on its own.
- **Auth failed.** An inline panel replaces the terminal:
  - Title: "web-prod refused your key".
  - Body: "The server rejected **id_ed25519** for **john**. The key may have been removed
    from the host's authorized_keys, or the user name changed."
  - Buttons: [Reinstall key] (opens the wizard at "Sign in once", prefilled), [Sign in with
    password this time], [Edit connection].
  - A small detail line: "Server offered: publickey, password".
- **Host key changed.** The session is blocked with the dialog in section 4.
- **Several sessions.** Example strip: [Home] [web-prod ●] [web-prod · 2 ●] [imaging-01 ○]
  [Keys].

### Add connection: the key install wizard

A 560px dialog with a step indicator ("1 Host · 2 Identity · 3 Sign in · 4 Verify"). Open
sessions keep running behind it.

**Step 1, "Host details"**

| Field | Notes |
|---|---|
| Host | Helper: "Name or IP address, as reachable from this PC." Example `192.168.10.42`. |
| Port | 22 |
| User | Prefilled from the default user, for example `john` |
| Name | Auto-filled from the host, for example `web-prod` |
| Folder | A tree select of folders, "Ungrouped", and "New folder…" |
| Tags | A token field with autocomplete from existing tags. Typing `#` is optional. Helper: "Lower-case letters, digits, dot, dash and underscore." |
| Colour label | Six swatches. Helper: "The colour frames the terminal, so you can tell at a glance which host you are typing into." |
| Sign in with | "Default key (id_ed25519)", another key, or "Password only" |
| Jump via | "None", or a saved connection. Helper: "Port-Key connects to that host first and reaches this one through it." |

- Checkbox: "My key is already on this host". Helper: "Skips the password step. Port-Key checks
  key login either way."
- Primary button: [Next]. Port-Key then connects (through the jump host if one is set), reads
  the host key and silently tries key login.

**Step 2, "Check host identity"**

- Skipped, with a small note, when `known_hosts` already has a matching key: "Host key matches
  your known_hosts entry."
- Otherwise the first-seen confirmation appears (copy in section 4).
- If the key already works: "Your key is already installed on web-prod. Port-Key signed in with
  it, so no password is needed." with [Save connection]. This jumps to the final step.

**Step 3, "Sign in once"**

- Body: "Your key is not on **web-prod** yet. Enter the password for `john` and Port-Key
  installs it for you."
- Password field, labelled "Password for john@192.168.10.42".
- A "What happens next" box:
  1. Port-Key signs in with this password.
  2. It adds the public half of `id_ed25519` to `~/.ssh/authorized_keys` on the host.
  3. It signs in again using only the key, to prove it works.
- Helper: "The password stays in memory until this wizard closes. It is never saved and never
  written to a log."
- Button: [Install key].
- If the server asks for a verification code (2FA), a second field appears with the server's
  own prompt text as its label.

**Step 4, "Verify key login"**

A checklist that fills in live:

- "Signed in with password ✓"
- "Added key to ~/.ssh/authorized_keys ✓" (or "Key was already in authorized_keys")
- "Signed in again with the key only ✓"
- "Password discarded ✓"

**Final state, "Connection saved"**

- A summary card: `web-prod`, `john@192.168.10.42 · Office LAN / Servers`, key `id_ed25519`.
- "From now on, one click opens this host with your key. No password needed."
- Buttons: [Open terminal] (primary) and [Add another].

**Manual path.** With "My key is already on this host" ticked, step 3 is skipped. If key login
then fails, the user sees "Key login was refused" with three choices:

- [Install it with a password]
- [Copy public key] together with [Copy install command], which copies
  `mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '<key>' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys`
- [Save as password-only]

**Failures.** Each failure appears inline in the step where it happens. All server output goes
in a collapsed "Details" block.

| Failure | Copy | Actions |
|---|---|---|
| Host unreachable (timeout) | "Couldn't reach 192.168.10.31 on port 2222. The connection timed out after 10 seconds. Check that the host is on and, for VPN hosts, that the VPN is connected." | [Try again] [Edit host] |
| Connection refused | "192.168.10.31 refused the connection on port 2222. The host is up but nothing is listening for SSH on that port. Check the port number or start sshd on the host." | [Try again] [Edit host] |
| Name not found | "Couldn't find a host named parser-vm. Check the spelling or use its IP address. Port-Key looks names up from inside its container, so names that exist only in the Windows hosts file may not resolve." | [Try again] [Edit host] |
| Jump host failed | "Couldn't reach web-prod because the jump host build-box didn't connect." followed by the jump host's own failure text. | [Try again] [Edit host] |
| Wrong password | "web-prod rejected the password for john. Check the user name and password, then try again." The password field is cleared and focused. | - |
| Password login disabled | Title "backup-nas does not accept passwords". Body: "The server only allows key logins, so Port-Key can't install your key for you. Add the key on the host yourself, for example from its console, then check again." A read-only box shows the install command, labelled "Run this on backup-nas as admin". | [Copy install command] [Copy public key only] [Check key login] |
| Key installed but still refused | "Port-Key added your key, but web-prod still refused it. The usual cause is permissions: sshd ignores authorized_keys if other users can write to it or to ~/.ssh." | [Fix permissions and retry], with the helper "Runs chmod go-w on ~/.ssh and ~/.ssh/authorized_keys as john", and [Save as password-only] |
| Can't write authorized_keys | "Port-Key signed in but couldn't write ~/.ssh/authorized_keys (Read-only file system). Some appliances store SSH keys elsewhere. Check the device's documentation for how to add a key." | [Save as password-only] |
| Not a POSIX shell | "web-prod doesn't offer a standard shell, so Port-Key can't install the key for you. Add the public key on the host yourself, then select Check key login." | [Copy public key] [Check key login] |
| Password expired | "john's password on web-prod has expired and must be changed. Open a password session to change it, then run this wizard again." | [Open password session] |
| Host key changed | The dialog in section 4. | |

### Edit connection

The same fields as wizard step 1 (Host, Port, User, Name, Folder, Tags, Colour label, Sign in
with, Jump via) plus Notes and a Pinned toggle, in one dialog with [Save] and [Cancel].

- "Jump via" lists every saved connection except this one and any that would create a loop.
- Saving a chain that is too long: "Jump chains can be at most 3 hosts long."
- Deleting a connection that others use as a jump host is blocked: "build-box is the jump host
  for 2 connections: web-prod, parser-vm. Change their Jump via setting first."

### Suggestions from known_hosts

- Entries that share a key are merged into one row: `parser-vm · 192.168.10.27` with "ED25519".
  The port is shown if it is not 22.
- Hosts that are already saved are hidden.
- Each row has [Add] and [Dismiss]. [Add] opens the wizard with the host filled in and the host
  key already trusted. [Dismiss] hides the row and shows an Undo toast.
- Footer when needed: "6 hashed entries can't be listed. Their names are hidden by
  HashKnownHosts."

### Keys tab

- Header: "Keys", with "Private keys stay on this PC. Port-Key only ever sends the public half
  to a server." and a [Generate key] button.

**Table columns**

| Column | Content |
|---|---|
| Name | Monospace file name, with a brass "Default" pill on the default key |
| Type | ED25519 or RSA 4096 |
| Fingerprint | `SHA256:…` with its colour strip |
| Source | "Found in .ssh", or "Made in Port-Key" in brass |
| Passphrase | "None", "Yes, locked" or "Yes, unlocked" |
| Used by | "5 connections" or "Not used" |

**Detail pane** for the selected key:

- The file name and its Windows path, for example `C:\Users\john\.ssh\id_ed25519`.
- The public key in a read-only monospace box.
- Buttons: [Copy public key] [Copy install command] [Install on a host…] [Set as default]
  [Hide from Port-Key].
- "Used by" followed by connection names as links.

A note under the table: "Keys found in your .ssh folder are read-only here: Port-Key never
changes or deletes them. Keys made in Port-Key are saved to `C:\Users\john\.ssh\port-key` so
Windows ssh can use them too."

**Hiding a key.** Port-Key never deletes a key file. Any key can be hidden from Port-Key, which
never touches the file.

- If the key is in use, the dialog says: "id_ed25519_portkey is used by 3 connections:
  web-prod, vpn-gw, imaging-01. Choose what they use instead:" followed by a key dropdown or
  "Password only".
- Hidden keys are listed behind a "Show hidden keys" link with [Unhide].

**[Generate key] dialog**

- Type: Ed25519 ("Recommended. Small, fast and accepted by every current server.") or RSA 4096
  ("For old appliances that do not accept Ed25519.").
- File name: `id_ed25519_portkey`. The name is checked live. Helper: "Saved to
  C:\Users\john\.ssh\port-key. Port-Key never overwrites an existing file."
- Comment: `john@WORKPC port-key 2026-11-14`.
- Passphrase (optional) and Confirm passphrase. Helper: "A passphrase protects the key file if
  someone copies it. Port-Key asks for it once and can keep the key unlocked while it runs."
- Buttons: [Cancel] and [Generate key].
- Name already taken: "A key named id_ed25519_portkey already exists. Choose another name."

### Passphrase prompt

A small dialog when connecting:

- Title: "Unlock id_ed25519".
- Text: "Enter the passphrase for this key to connect to web-prod."
- Checkbox, on by default: "Keep unlocked for 8 hours". Helper: "Held in memory only. Lock it
  any time from the top bar."
- Buttons: [Unlock and connect] and [Cancel].
- Wrong passphrase: "That passphrase doesn't unlock id_ed25519. Try again." The field is
  cleared.

While a key is held unlocked, the top-bar indicator offers Lock.

### Quick-connect palette

- A text field over a list of matching connections. Each row shows the colour chip, the name,
  the address in monospace and when it was last used. A host with open sessions says
  "2 sessions open · Enter to focus".
- The last row is always "Add `<text>` as a new connection…", which opens the wizard with the
  text parsed into user, host and port.
- It also lists commands: Generate key, Keys, Settings, Lock keys.
- Footer hints: "↑↓ to move", "Enter to connect", "Shift+Enter for another session", "Esc to
  close".

### Settings tab

Only these settings earn a place:

- Terminal font size.
- Default user.
- Default key.
- "Ctrl+C copies and Ctrl+V pastes, like Windows Terminal" (on).
- "Warn before leaving with connected sessions" (on).
- Change password: Current password, New password, Confirm, [Change password]. "Changing the
  password signs out every other browser."
- Trusted host keys: a list of stored host keys (host, "via build-box" for a host reached
  through a jump host, type, fingerprint with strip, trusted since) with [Forget], which asks
  the user to type the host name. A known_hosts key the owner replaced is listed as "Not
  trusted: you replaced this known_hosts key"; forgetting it lets known_hosts trust it again.
- A link to Diagnostics.
- A read-only About block showing the `.ssh` path, the data location and the version.

There is no theme setting. v1 has a dark theme only.

### Diagnostics tab

A read-only page for the first run on the PC and for troubleshooting:

- The mounted `.ssh` folder: found or not, and how many keys were found.
- Whether the generated-keys folder is writable.
- Keys that could not be read, each with a reason: "PuTTY format, not supported", "Not a
  private key", "Unreadable".
- A note when no key file is found: "If your key lives only in the Windows ssh-agent, Port-Key
  can't reach it. Generate a key here and install it with the wizard."
- A "Check a host" field: enter a name or address and Port-Key reports what it resolves to from
  inside the container and whether the port answers.
- The app version.

## 3. Interactions

### Sidebar clicks

- A single click on a row connects. If a session to that host is already open, it focuses that
  session instead. A double-click is debounced to one action and has no other meaning.
- Ctrl+click or middle-click opens an extra session.
- Right-click or "⋯" opens a menu: Connect, New session, Edit, Duplicate, Pin, Copy ssh command
  (`ssh -p 2222 admin@192.168.10.31`), Delete.

### Palette

- Ctrl+Shift+K anywhere; Ctrl+K when the terminal does not have focus.
- Fuzzy search over name, host, user, folder path, tags and notes, ranked by recent use.
  A `#prod` token narrows the list to connections tagged `prod`.
- Enter connects. Shift+Enter opens an additional session.

### Other shortcuts

- Alt+1…9 switches tabs. Alt+0 goes to Home.
- Ctrl+Shift+F searches the scrollback.
- `/` focuses the sidebar filter when the terminal does not have focus.

**Shortcut rule.** App shortcuts use only Ctrl+Shift or Alt combinations, and every other key
goes to the shell. The browser keeps Ctrl+W, Ctrl+T and Ctrl+Tab, so a leave warning protects
connected sessions. Edge uses Ctrl+Shift+K for Duplicate tab; if it cannot be overridden there,
the palette falls back to Ctrl+Shift+Space.

### Copy and paste (Windows Terminal behaviour)

- Ctrl+C copies when text is selected and sends an interrupt otherwise.
- Ctrl+V pastes.
- Right-click copies when text is selected and pastes otherwise.
- Ctrl+Insert and Shift+Insert always work.
- Pasting more than one line asks first: "Paste 14 lines into web-prod?" with [Paste] and
  [Cancel].

### Confirmation rules

- Actions that can be undone (delete connection, dismiss suggestion) happen immediately and
  show a 10-second Undo toast. Open sessions for a deleted connection stay open with
  "(deleted)" after the name. Undo of a delete puts the same connection back (same id, tags,
  usage), so its open tabs lose "(deleted)" and Reconnect works again.
- Closing one tab asks nothing.
- "Close all sessions" asks with the count.
- Actions that can't be undone ask for typed confirmation: replacing a host key, forgetting a
  host key.

### Focus

- After connecting, focus goes into the terminal.
- After closing a tab, focus moves to the tab on its left.
- Dialogs trap focus on their first field. In danger dialogs, the safe button has initial
  focus.
- Esc closes dialogs and the palette. Inside the terminal, Esc always goes to the shell.

## 4. Key copy

### Host key, first seen

- Title: "Check this host's identity".
- Body: "This is the first time Port-Key has connected to web-prod (192.168.10.42). It
  presented this key:"
- Fingerprint: `ED25519 SHA256:pZ8mQ1c…Tw4E`, with its colour strip.
- "To be sure, run this on the server and compare:"
  `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
- "Port-Key remembers this key and stops you if the host ever presents a different one. Nothing
  has been sent to the host yet."
- Buttons: [Trust this host] [Cancel].
- For a jump host, the title names it: "Check the jump host's identity", and the body starts
  "web-prod is reached through build-box (192.168.10.20)."

### Host key changed

- Title: "web-prod's host key has changed".
- Body: "The key web-prod (192.168.10.42) presented doesn't match the one you trusted on
  2 March 2026. This is expected after a reinstall or when an IP address is reused. It is also
  what an intercepted connection looks like."
- In bold: "Port-Key has sent no password and no key. The connection is closed."
- The old and new fingerprints are shown side by side, labelled "Trusted since 2 March 2026"
  and "Presented just now", each with its colour strip.
- When the old key is a line in the owner's known_hosts (labelled "In your known_hosts file"):
  "Replacing it makes Port-Key stop trusting the old key. Your known_hosts file is not changed;
  to stop Windows ssh trusting the old key too, run `ssh-keygen -R 192.168.10.42`."
- A field: "If you know why it changed, type **web-prod** to replace the stored key."
- Buttons: [Replace key and connect], enabled only when the typed name matches, and
  [Keep it closed], which has initial focus.

### Button labels

Add connection, Connect, Reconnect, Install key, Trust this host, Save connection, Open
terminal, Copy public key, Copy install command, Generate key, Unlock and connect, Set
password, Sign in.

### Toasts

A toast after a copy always says what was copied: "Public key copied", "Install command
copied", "ssh command copied".

### Voice

Plain, specific, active voice. No exclamation marks. Every error says what happened and what to
do next.

## 5. Visual direction

### Palette (dark, the only theme in v1)

Every colour is a CSS custom property so a light theme is cheap to add later.

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0d1117` | Page |
| `--panel` | `#161b22` | Top bar, sidebar, dialogs |
| `--panel-2` | `#1c2129` | Selected rows |
| `--border` | `#30363d` | Borders |
| `--text` | `#c9d1d9` | Body text |
| `--text-strong` | `#e6edf3` | Names and headings |
| `--muted` | `#8b949e` | Secondary text |
| `--accent` | `#2f81f7` | Interaction; links use `#58a6ff` |
| `--accent-fill` | `#1f6feb` | Primary buttons, with white text |
| `--brass` | `#e3b341` | Key-related things only |
| `--green` | `#3fb950` | Connected, reachable, done |
| `--red` | `#f85149` | Danger; red text uses `#ff7b72` |
| `--amber` | `#d29922` | Connecting, warnings |
| `--term-bg` | `#010409` | Terminal background |

- Blue is the colour for interaction.
- **Brass** is Port-Key's own colour and is used only for key-related things: the wordmark key,
  the auth glyph, the "Made in Port-Key" badge, key names and the unlocked-keys indicator.
- Colour-label choices: red, amber, green, blue, purple, grey.
- Colours that must be told apart also differ in shape or lightness (filled dot versus ring).

### Type

- The system UI font at 13px.
- Monospace for every technical value: hosts, users, ports, paths, fingerprints.
- The terminal defaults to Cascadia Mono with a `ui-monospace` fallback, at 14px.

### Density

- 44px sidebar rows, a compact session bar, 6px radius (10px on dialogs).
- No shadows except on menus and dialogs.

### Distinctive touches

1. **Colour labels as a safety signal.** The connection's colour runs from its sidebar stripe
   through the tab's top edge to a 2px frame around the terminal. Red for production means the
   user always knows which box they are typing into.
2. **Fingerprint strips.** Each fingerprint gets a deterministic 8-segment colour strip drawn
   from its bytes. A changed host key looks visibly different at a glance.

## 6. Scope

### In v1

- Saved connections in nested folders (up to 3 levels) with tags, a colour label, notes,
  pinned and a default user; bulk move and tag from Home.
- Duplicate connection.
- Terminal sessions as tabs, surviving a reload for 15 minutes.
- Key install wizard, including 2FA prompts and installing through a jump host.
- Jump hosts ("Jump via").
- Suggestions from known_hosts.
- Keys tab with key generation and hiding keys.
- Passphrase unlock, kept for 8 hours.
- Quick-connect palette.
- Scrollback Find.
- Reachability dots.
- Multi-line paste guard.
- App password, change password, forget host key.
- Diagnostics.

### Not in v1

- Connecting once to a host without saving it.
- Installing Port-Key as an app (PWA).
- Split panes.
- Automatic reconnect.
- SFTP and file transfer.
- Port forwarding.
- Snippets.
- Session logging.
- Export and import of connections.
- Import from `~/.ssh/config`.
- Keys that live only in the Windows ssh-agent, and PuTTY `.ppk` keys.
- Deleting key files.
- A light theme.
- More than one folder per connection, and colours on tags.
- Broadcasting input to several sessions.
