# Handoff: the first-visit setup code

Package key `setup-code`, 2026-11-16, on `dev/v1`. The owner approved package 1's open request
(`docs/handoff/phase-a.md`, "Open decisions"). Contract: `docs/api.md` "Auth" and decision 17;
threat model: `docs/architecture.md` section 4, "App login"; screen: `docs/ux-spec.md` and
`docs/design/SetPassword.dc.html`.

## What it does

- While no app password exists, `Services.startup` asks `PasswordAuth.new_setup_code()` for a
  fresh code (`secrets.choice` over `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`, 12 symbols, 60 bits,
  `XXXX-XXXX-XXXX`) and `setup_code.announce` prints it once, framed, on the `portkey.setup`
  logger. `__main__.log_config()` routes that logger to uvicorn's stderr handler at INFO,
  whatever `PORTKEY_LOG_LEVEL` says. The code lives only in `PasswordAuth._setup_code`.
- `POST /api/auth/setup {password, code}`: `already_setup` (409), then the per-client backoff
  shared with login (429), then `setup_needs_restart` (409, no code held), then the code
  (`setup_code.matches`: SHA-256 of both normalised sides, `hmac.compare_digest`; wrong is 403
  `bad_setup_code` and counts as a failure). Success drops the code; later starts print none.
- `GET /api/auth/status` adds `setup_code`: `"required"`, `"restart"` or `null`. Never the code.
- `redact.hide_setup_code(code)` makes the redaction filter scrub the live code (any case, any
  separators) from every record except those on `portkey.setup`.
- Reset while running: `reset_password` deletes the password, the running app holds no code
  (a password existed at its start), so setup answers `setup_needs_restart` until
  `docker compose restart portkey` prints a fresh one. The CLI says so. Checked in the real
  runtime image with a named volume: setup, reset, refused, restart, new code works, old one
  does not, and no code is printed after a restart once a password exists.

## For whoever works on it next

- Tests that need a token without going through setup use `services.tokens.issue()` (the
  session and wizard fixtures do). Tests of setup itself read the code from the captured log:
  `tests/unit/security/setup_log.py` (`codes_in_log`, `running`) and the `setup_code` fixture
  in `tests/unit/security/conftest.py`.
- e2e: the e2e container has no Docker access, so `./mk e2e` starts the stack, runs
  `docker compose logs app` and passes it in `PORTKEY_APP_LOG`; `tests/e2e/harness/app_log.py`
  reads the newest "Setup code:" line with its own pattern (a change to what the app prints
  fails the spec). `AppApi.setup`/`ensure_password` and `set_app_password_ui` take the code
  from there. The e2e `app` keeps `/data` in a tmpfs, so a container restart there wipes the
  password and prints a new code.
- `tests/unit/security/test_setup_code_log.py::test_the_compose_service_name_matches_messages_and_the_form`
  ties `setup_code.COMPOSE_SERVICE` to `compose.yaml`, the server messages, `login.js` and the
  README.

## Decisions taken here (the owner may want to revisit)

- The helper text says `docker compose logs portkey`, not `port-key` as the mockup had:
  `compose.yaml`'s service is `portkey` and the README, smoke checklist and `reset_password`
  already use that name. The mockup now says `portkey`.
- A wrong code is 403, not 401, so 401 keeps meaning "signed out" or a wrong password.
- `setup_code` in the status is three-valued so the form can explain the restart case up front.

## Left open

- As with login, every client behind Docker Desktop's port proxy shares one address, so a local
  process can lock the owner out of setup with wrong guesses (up to 10 minutes per lockout).
  It cannot set the password. *Corrected after the security review:* one more wrong guess
  each time a lockout ends keeps the owner out indefinitely, of setup and of sign-in; see
  `docs/architecture.md` section 4 "Left open" for the trade-off and the suggested fix.
- Found, not changed: `portkey.*` loggers have no handler in the container (uvicorn configures
  only its own loggers), so the app's own INFO lines (sessions opened, keys generated, wizard
  outcomes) never reach `docker compose logs`, and WARNING+ ones go out through Python's
  last-resort handler without a level prefix. Only `portkey.setup` is routed on purpose.
