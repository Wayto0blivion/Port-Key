# Port-Key HTTP API (frozen contract, v1)

The models are `portkey/api/schemas/`; this document is the same contract with examples. A
change to either is a contract change: write it in your package report, do not edit here.
WebSockets are in `docs/ws-protocol.md`.

## Conventions

- Base URL: `http://localhost:8822`. JSON only, UTF-8. Times are ISO-8601 UTC, `2026-11-15T14:02:00Z`.
- **Host** must be `localhost:8822`, `127.0.0.1:8822` or `[::1]:8822` (configurable): else 421.
- **Every request except GET/HEAD/OPTIONS** must carry `Origin` from the same list (else 403
  `origin_not_allowed`), `X-Port-Key: 1` (else 403 `missing_csrf_header`) and
  `Content-Type: application/json` (else 415 `unsupported_media_type`) with a JSON body: send
  `{}` when there are no fields, including on DELETE.
- **Auth**: `Authorization: Bearer <token>` on everything except `GET /api/health`,
  `GET /api/auth/status`, `POST /api/auth/setup` and `POST /api/auth/login`. Missing, expired or
  revoked token: 401 `unauthorized`. **401 always means "signed out"**; no other failure uses it
  except a wrong password on login or password change (`bad_password`).
- Request bodies reject unknown fields (422). Strings are trimmed where noted.
- Lists come wrapped in an object (`{"connections": [...]}`), never as a bare array.
- PATCH applies only the fields present; an explicit `null` clears a nullable field.
- `{key_id}` in key paths is the fingerprint without `SHA256:` in base64url (`+`->`-`,
  `/`->`_`), the `id` field of a key object.

### Errors

```json
{"error": {"code": "jump_in_use", "message": "build-box is the jump host for 2 connections: web-prod, parser-vm. Change their Jump via setting first.",
           "dependants": [{"id": "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a", "name": "web-prod"}]}}
```

Optional extras: `fields` (422 `validation`: `[{loc, msg, type}]`, never the input values),
`dependants`, `retry_after` (seconds, 429). `message` is user-facing copy.

| Status | Codes |
|---|---|
| 400 | `bad_passphrase` |
| 401 | `unauthorized`, `bad_password` |
| 403 | `origin_not_allowed`, `missing_csrf_header` |
| 404 | `not_found`, `key_unknown`, `session_gone` |
| 409 | `already_setup`, `not_setup`, `name_taken`, `jump_in_use`, `key_name_taken`, `key_in_use`, `key_not_encrypted` |
| 415 | `unsupported_media_type` |
| 421 | `host_not_allowed` |
| 422 | `validation`, `jump_cycle`, `jump_too_deep`, `jump_unknown`, `group_unknown`, `group_too_deep`, `group_cycle`, `tag_invalid`, `key_unknown`, `key_name_invalid`, `key_unreadable`, `confirm_mismatch` |
| 429 | `rate_limited` (login and password change) |
| 500 / 501 | `internal` / `not_implemented` (skeleton only) |

`validation` covers every request body that fails its schema, malformed tags included: the
schema checks the tag pattern before any repository runs, so a bad tag in a connection, bulk or
tag-rename body is 422 `validation` with `fields`. `tag_invalid` is left for the repository's
own checks: a bulk `add_tags` that would give a connection more than 32 tags, and direct
callers such as the wizard.

## Health

`GET /api/health` (no auth) -> `200 {"status": "ok", "version": "0.1.0"}`

## Auth

| Request | Response |
|---|---|
| `GET /api/auth/status` (token optional) | `200 {"setup": true, "authenticated": false}` |
| `POST /api/auth/setup {"password": "correct horse battery"}` | `200 {"token": "q3Jx...", "expires_at": "2026-12-15T14:02:00Z"}`; 409 `already_setup` |
| `POST /api/auth/login {"password": "correct horse battery"}` | `200 {"token", "expires_at"}`; 401 `bad_password`; 429 `rate_limited` with `retry_after`; 409 `not_setup` |
| `POST /api/auth/logout {}` | `204`; revokes this token |
| `POST /api/auth/password {"old": "...", "new": "..."}` | `204`; revokes every other token; 401 `bad_password`; 429 `rate_limited` with `retry_after` |

`setup` is true once an app password exists (false: show "Set a password for Port-Key").
`authenticated` says whether the request's bearer token is valid. Passwords are 8 to 1024
characters (`new`, `setup`). Tokens are valid 30 days, sliding (each use moves the expiry 30
days ahead), and never longer than 90 days after they were issued, however often they are
used; after that every call answers 401 `unauthorized` and the owner signs in again.
`expires_at` is the expiry at issue (30 days). Wrong current passwords on a password change
share one backoff, as failed logins do, so a stolen token cannot be used to guess the password.
A 401 `bad_password` (wrong password on login or password change) is not a sign-out.

## Connections

Connection object:

```json
{"id": "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a", "name": "web-prod", "host": "192.168.10.42", "port": 22,
 "username": "john", "auth_method": "key", "key_fp": null, "jump_id": "5a7d2e90-1c3b-4f6e-8d2a-9b0c1d2e3f4a",
 "group_id": "d4e5f6a7-1829-4b0c-9d8e-7f6a5b4c3d2e", "tags": ["prod", "web"], "color": "red", "pinned": true,
 "notes": "Production. Red on purpose.", "sort_order": 0, "created_at": "2026-11-02T09:15:00Z",
 "updated_at": "2026-11-14T10:00:00Z", "last_used_at": "2026-11-15T14:02:00Z", "use_count": 41}
```

`key_fp: null` means the default key. `group_id: null` is Ungrouped. `color` is one of `red`,
`amber`, `green`, `blue`, `purple`, `grey`. `tags` are lower-case, sorted.

| Request | Response |
|---|---|
| `GET /api/connections` | `200 {"connections": [Connection, ...]}` ordered pinned first, then sort_order, then name |
| `POST /api/connections` (below) | `201 Connection`; 409 `name_taken`; 422 `jump_*`, `group_unknown`, `key_unknown`, `validation` (including malformed tags) |
| `PATCH /api/connections/{id}` any subset of the create fields plus `sort_order` | `200 Connection`; same errors plus 404 |
| `DELETE /api/connections/{id} {}` | `204`; 404; 409 `jump_in_use` with `dependants` |
| `POST /api/connections/{id}/duplicate {}` | `201 Connection` named `web-prod (copy)`, then `(copy 2)`...; not pinned, no usage |
| `POST /api/connections/bulk` (below) | `200 {"connections": [updated...]}`; atomic; 404; 422 `group_unknown`, `tag_invalid` (over 32 tags), `validation` |
| `POST /api/connections/reachability {"ids": [...]}` | `200 {"results": [...]}` |

Create body (defaults shown):

```json
{"name": "web-prod", "host": "192.168.10.42", "port": 22, "username": "john", "auth_method": "key",
 "key_fp": null, "jump_id": null, "group_id": null, "tags": [], "color": "grey", "pinned": false, "notes": ""}
```

Rules: names 1-64 characters, unique ignoring case. `host` has no spaces, `@` or `/` (IPv6
literals without brackets). `notes` up to 2000 characters. A jump chain is at most 3 hops
before the target, without cycles (`jump_cycle`, `jump_too_deep`, `jump_unknown`). Tags: see
"Tags"; at most 32 per connection; created on first use. `key_fp` must name a key that
`GET /api/keys` lists and that is not hidden: a hidden key counts as unknown (422
`key_unknown`).

Bulk:

```json
{"ids": ["0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a", "5a7d2e90-1c3b-4f6e-8d2a-9b0c1d2e3f4a"],
 "set_group_id": "c3d4e5f6-0718-4a9b-8c7d-6e5f4a3b2c1d", "add_tags": ["office"], "remove_tags": ["old"]}
```

Omit `set_group_id` to leave folders alone; `null` moves to Ungrouped. 1-1000 ids. Any
unknown id (404) or invalid value (422) changes nothing.

Reachability is a TCP connect to each connection's SSH port (2 s timeout). Connections with a
jump are `skipped`. Unknown ids are left out. At most 500 ids. `ms` is present only when
`state` is `up`; `down` and `skipped` results leave it out (not `null`).

```json
{"results": [{"id": "0b6f3c1e-...", "state": "up", "ms": 3}, {"id": "9e8d7c6b-...", "state": "down"},
             {"id": "5a7d2e90-...", "state": "skipped"}]}
```

## Folders

The UI calls groups folders. At most 3 levels; sibling names unique ignoring case.

| Request | Response |
|---|---|
| `GET /api/groups` | `200 {"groups": [{"id": "c3d4e5f6-...", "name": "Office LAN", "parent_id": null, "sort_order": 0, "connection_count": 9}, {"id": "d4e5f6a7-...", "name": "Servers", "parent_id": "c3d4e5f6-...", "sort_order": 0, "connection_count": 4}]}` (flat; counts include descendants) |
| `POST /api/groups {"name": "Servers", "parent_id": "c3d4e5f6-..."}` | `201 Group`; 409 `name_taken`; 422 `group_unknown`, `group_too_deep` |
| `PATCH /api/groups/{id} {"name"?, "parent_id"?, "sort_order"?}` | `200 Group`; `parent_id: null` moves to the root; 422 `group_cycle` (under itself or a descendant), `group_too_deep`; 409 `name_taken` |
| `DELETE /api/groups/{id} {}` | `204`; child folders and connections move to its parent (root / Ungrouped if none); never deletes connections; a moved folder whose name clashes with a new sibling gets " (2)", " (3)"... |

## Tags

Stored lower-case, pattern `[a-z0-9][a-z0-9._-]{0,31}`. Input is trimmed, a leading `#` is
dropped and it is lower-cased before validation. A tag exists while at least one connection
uses it.

| Request | Response |
|---|---|
| `GET /api/tags` | `200 {"tags": [{"name": "prod", "count": 6}, {"name": "web", "count": 2}]}` most used first |
| `PATCH /api/tags/prod {"name": "production"}` | `200 {"name": "production", "count": 6}`; merges into an existing tag of that name; 404 `not_found` when no connection uses `prod`; 422 `validation` for a malformed new name |
| `DELETE /api/tags/prod {}` | `204`; removed from every connection; 404 `not_found` when no connection uses it |

## Keys

Key object (`path` is the Windows path):

```json
{"id": "Xk3fP0aB7cD9eF1gH2iJ4kL6mN8oP0qR2sT4uV6r9Qa", "fp": "SHA256:Xk3fP0aB7cD9eF1gH2iJ4kL6mN8oP0qR2sT4uV6r9Qa",
 "name": "id_ed25519", "label": null, "type": "ed25519", "bits": 256, "comment": "john@WORKPC",
 "path": "C:\\Users\\john\\.ssh\\id_ed25519", "source": "ssh_dir", "encrypted": false, "unlocked": false,
 "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... john@WORKPC", "is_default": true, "hidden": false,
 "used_by": [{"id": "0b6f3c1e-...", "name": "web-prod"}]}
```

| Request | Response |
|---|---|
| `GET /api/keys` | `200 {"keys": [Key...], "problems": [{"path": "C:\\Users\\john\\.ssh\\work.ppk", "reason": "ppk", "detail": ""}], "default_fp": "SHA256:Xk3f...", "unlocked_count": 0}`; hidden keys included with `hidden: true` |
| `GET /api/keys/{key_id}/public` | `200 text/plain` `ssh-ed25519 AAAA... john@WORKPC\n`; 404 `key_unknown` |
| `GET /api/keys/name-check?name=id_ed25519_portkey` | `200 {"name": "id_ed25519_portkey", "available": false, "reason": "taken"}` (`reason`: `taken`, `invalid`, null) |
| `POST /api/keys/generate` (below) | `201 Key` (`source: "generated"`); 409 `key_name_taken`; 422 `key_name_invalid` |
| `PATCH /api/keys/{key_id} {"label"?, "hidden"?, "reassign"?}` | `200 Key`; 409 `key_in_use` with `dependants` when hiding a used key without `reassign`; 422 `key_unknown` when `reassign.key_fp` is hidden or not listed. One transaction: on any error nothing changes, the label included |
| `POST /api/keys/{key_id}/unlock {"passphrase": "...", "remember": true}` | `204`; 400 `bad_passphrase`; 409 `key_not_encrypted` |
| `POST /api/keys/lock {}` | `204`; drops every unlocked key |

Generate: `{"name": "id_ed25519_portkey", "type": "ed25519", "bits": null, "comment":
"john@WORKPC port-key 2026-11-14", "passphrase": null}`. `name` matches
`[A-Za-z0-9_][A-Za-z0-9._-]*`, at most 64, not ending `.pub`/`.ppk`. `bits`: ed25519 null; rsa
2048/3072/4096 (default 4096); ecdsa 256/384/521 (default 256). Files are written to
`.ssh\port-key` and never overwrite.

Hide with reassign: `{"hidden": true, "reassign": {"auth_method": "key", "key_fp": "SHA256:..."}}` or
`{"hidden": true, "reassign": {"auth_method": "password"}}`. Connections using the key (including
those on the default key when it is the default) are repointed in the same transaction; if it
was the default key the default is cleared. Unlock `remember: true` holds the key for
`passphrase_ttl`; `false` for 60 seconds.

## Hosts

| Request | Response |
|---|---|
| `GET /api/hosts/suggestions` | `200 {"suggestions": [{"host": "parser-vm", "port": 22, "aliases": ["192.168.10.27"], "key_types": ["ssh-ed25519"]}], "hashed_count": 6}`; dismissed hosts and hosts a saved connection already uses (by host or alias, same port) are left out |
| `POST /api/hosts/suggestions/dismiss {"host": "parser-vm", "port": 22}` | `204` |
| `POST /api/hosts/suggestions/restore {"host": "parser-vm", "port": 22}` | `204` (Undo) |
| `GET /api/hostkeys` | `200 {"host_keys": [{"id": 7, "host": "192.168.10.42", "port": 22, "key_type": "ssh-ed25519", "fp_sha256": "SHA256:pZ8m...", "public_key": "ssh-ed25519 AAAA...", "first_seen_at": "2026-03-02T10:00:00Z", "last_seen_at": "2026-11-15T14:02:00Z"}]}` |
| `DELETE /api/hostkeys/{id} {"confirm_host": "192.168.10.42"}` | `204`; 422 `confirm_mismatch`; 404 |

Only host keys trusted in Port-Key are listed; the owner's `known_hosts` is never changed.
`confirm_host` is compared to the record's host ignoring case.

## Sessions

| Request | Response |
|---|---|
| `GET /api/sessions` | `200 {"sessions": [{"session_id": "s_4Zq...", "connection_id": "0b6f...", "started_at": "2026-11-15T14:02:00Z", "attached": true, "detached_at": null}]}` |
| `DELETE /api/sessions/{id} {}` | `204` kills the shell and every hop; 404 `session_gone` |

## Settings

```json
{"default_key_fp": null, "default_user": "john", "passphrase_ttl": 28800, "detach_grace": 900,
 "font_size": 14, "windows_copy_paste": true, "leave_warning": true,
 "about": {"ssh_dir": "C:\\Users\\john\\.ssh", "keys_dir": "C:\\Users\\john\\.ssh\\port-key",
           "data_dir": "/data", "version": "0.1.0"}}
```

`GET /api/settings` -> `200` the above. `PATCH /api/settings` with any subset (not `about`) ->
`200` the above. Limits: `passphrase_ttl` 60-604800 s, `detach_grace` 60-86400 s, `font_size`
8-32, `default_user` without spaces. `default_key_fp` must name a known key that is not
hidden (422 `key_unknown`); `null` falls back to id_ed25519, id_ecdsa, id_rsa, then name order.

## Diagnostics

`GET /api/diag` ->

```json
{"version": "0.1.0", "python": "3.12.8", "asyncssh": "2.18.0",
 "ssh_dir": {"path": "C:\\Users\\john\\.ssh", "present": true, "readable": true, "writable": false,
             "key_count": 1, "known_hosts": true, "mode_note": "Files in the mounted folder show as mode 0777. Port-Key does not need stricter modes."},
 "keys_dir": {"path": "C:\\Users\\john\\.ssh\\port-key", "present": true, "readable": true, "writable": true},
 "data_dir": {"path": "/data", "present": true, "readable": true, "writable": true},
 "key_problems": [{"path": "C:\\Users\\john\\.ssh\\work.ppk", "reason": "ppk", "detail": "PuTTY format, not supported"}],
 "notes": []}
```

`POST /api/diag/resolve {"host": "build-box", "port": 22}` ->

```json
{"host": "build-box", "port": 22, "addresses": ["192.168.10.20"], "resolve_error": null,
 "tcp": {"state": "up", "ms": 4, "error": null}}
```

A name that does not resolve: `addresses: []`, `resolve_error: "Name or service not known"`,
`tcp: {"state": "skipped", "ms": null, "error": null}`.

## Decisions

Where `docs/architecture.md` was silent or ambiguous, package 0 decided:

1. All list responses are objects (`{"keys": [...]}`, `{"sessions": [...]}`, `{"host_keys":
   [...]}`, `{"groups": [...]}`, `{"tags": [...]}`), so fields like `problems` can sit beside
   them. The architecture table showed bare arrays for keys, sessions and folders/tags.
2. Every non-GET request carries a JSON body (`{}` if empty), so the Content-Type rule has no
   exceptions. DELETE bodies are allowed.
3. Fingerprints in URLs use the base64url `id`, because `/` and `+` in `SHA256:...` break paths.
4. Added `GET /api/keys/name-check` (the live name check in the Generate key dialog) and
   `POST /api/hosts/suggestions/restore` (the Undo toast after Dismiss).
5. `GET /api/keys` returns `problems`, `default_fp` and `unlocked_count` too (first-run "only
   unsupported keys" message, Keys tab, top-bar indicator).
6. Hiding a key that connections use takes `reassign` and is atomic, instead of the client
   patching each connection.
7. Connection names are unique ignoring case (the typed-name host-key confirmation needs an
   unambiguous name); duplicate names get " (copy)".
8. 401 is reserved for "signed out" and wrong app passwords; a wrong key passphrase is 400
   `bad_passphrase` so the client does not sign out.
9. `POST /api/keys/{id}/unlock` with `remember: false` holds the key for 60 seconds.
10. Settings: `copy/paste mode` is the boolean `windows_copy_paste`, the leave warning is
    `leave_warning`, and `GET /api/settings` includes a read-only `about` block.
11. `GET /api/health` (no auth) exists for the container healthcheck; it still needs an allowed
    Host header. `GET /api/openapi.json` is served; the docs UIs are not.
12. Unknown ids in reachability are left out of `results`.

Decided at the phase A integration (2026-11-16):

13. Malformed tags in a request body are 422 `validation` (schema), not `tag_invalid`.
14. Suggestions no longer carry `saved`: the server leaves saved hosts out, so it was always
    false.
15. Tokens have an absolute lifetime of 90 days on top of the 30-day sliding expiry.
16. Hiding a key with `reassign` is one database transaction, label change included.
