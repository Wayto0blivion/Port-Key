# Contributing to Port-Key

Read `docs/PLAN.md` and `docs/architecture.md` first. The wire contracts are `docs/api.md` and
`docs/ws-protocol.md`; screens and copy are in `docs/ux-spec.md`. A change to an endpoint, a
message or a screen updates the matching document in the same pull request.

## Everything runs in containers

Nothing is installed on the host and no project code runs on it. Builds, tests, linters and the
lock-file generator all run in containers driven by `compose.dev.yaml` and the `Makefile`. If
your machine has no `make`, `./mk` runs make in a small container (it uses the host's `make`
when there is one). No dev service publishes a port on the host.

Always pass your own compose project name with `P=`, starting with `portkey-` (for example
`portkey-me`, or `portkey-me-e2e` for a second run at the same time), so parallel runs do not
share containers, networks or volumes:

```sh
./mk build P=portkey-me         # build the dev and lint images (again after a lock change)
./mk check P=portkey-me         # ruff check + ruff format --check + mypy + unit tests
./mk unit P=portkey-me          # unit tests only
./mk lint-js P=portkey-me       # eslint + tsc --checkJs on portkey/static/js
./mk frontend P=portkey-me      # tests/frontend: Chromium against a mock API, no network
./mk integration P=portkey-me   # sshd containers + tests/integration
./mk e2e P=portkey-me           # the real app image + sshd + Playwright
./mk clean P=portkey-me         # stop and remove that project's containers and volumes
```

Narrower runs:

```sh
./mk fmt P=portkey-me FILES="portkey/keys tests/unit/keys"     # ruff format + ruff check --fix
./mk check P=portkey-me FILES="portkey/keys" TESTS=tests/unit/keys   # mypy always runs on all
./mk frontend P=portkey-me FRONTEND=tests/frontend/terminal    # one directory or file
```

A single test, with `HOST_UID=$(id -u) HOST_GID=$(id -g)` exported first (otherwise the
container runs as uid 1000):

```sh
docker compose -f compose.dev.yaml -p portkey-me run --rm --no-deps test \
    pytest tests/unit/keys -k textio
```

The images are shared (`portkey-dev:local`, `portkey-lint-js:local`, `portkey-e2e:local`) and
the source is bind-mounted, so code changes need no rebuild. Dependencies are hash-locked:
change `pyproject.toml`, then `./mk lock` (regenerates `requirements*.lock` in a container)
and `./mk build`. The test SSH servers, users and fixture keys are described in
`tests/fixtures/README.md`; everything under `tests/fixtures/TEST-ONLY/` is test material and
protects nothing.

## Before a pull request

Run `./mk check` and, if you touched JavaScript, CSS or `index.html`, `./mk lint-js` and
`./mk frontend`. Changes to SSH, sessions or the wizard also need `./mk integration`, and
anything a user can see end to end needs `./mk e2e`. Finish with `./mk clean` for every
project name you used.

## Conventions

- No file over about 400 lines; split a module into private modules in its own package.
- Async code on a hot path (SSH connect, terminal sessions, the wizard) calls the SQLite
  repositories through `asyncio.to_thread`; see the header of `portkey/interfaces.py`.
- Secrets (passwords, passphrases, private keys, tokens, the setup code) are never logged,
  never put in an exception message and never stored; `docs/architecture.md` section 4 lists
  the exceptions.
- Code comments and test names refer to the v1 work packages by number ("package 7");
  `docs/architecture.md` section 11 maps them to areas of the code.
