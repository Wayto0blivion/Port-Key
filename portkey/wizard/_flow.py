"""One wizard run: the state machine behind ``InstallWizard.run``.

::

    validate -> first connection (key, then password unless assume_installed)
      key works .................................... save, already_installed
      key refused, assume_installed, password
      or keyboard-interactive offered ............. needs_action key_refused
          install_with_password -> password connection -> install
          save_password_only ...................... save, saved_password_only
      key refused, no password methods ............ needs_action password_not_allowed
          check_key (repeatable) ................... save, already_installed
      signed in with the password ................. install
    install (sh -s on the password connection)
      non_posix ................................... needs_action non_posix (check_key)
      readonly / failed ........................... needs_action authorized_keys_readonly
      added / already_present ..................... verify
    verify (new key-only connection)
      refused ..................................... needs_action key_still_refused
          fix_permissions (once) -> chmod go-w, verify again
          save_password_only
      works ....................................... discard_password, save, installed

``cancel`` is accepted at every ``needs_action`` and ends the run with ``PromptCancelled``.
Whatever happens, every connection the run opened is closed when it ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from portkey.api.schemas.ws import NeedsActionMsg, StartMsg, WizardAction
from portkey.interfaces import Prompter, SshChain
from portkey.models import ConnectionRecord, HopRef, HopTarget, KeyRecord
from portkey.ssh.errors import (
    AuthFailed,
    BadMessage,
    HostKeyRejected,
    PasswordNotAllowed,
    PromptCancelled,
    SshError,
    WsError,
)
from portkey.wizard import _copy
from portkey.wizard._remote import run_check, run_install, run_sh
from portkey.wizard._steps import SECRET_METHODS, Attempt, Reporter, StepPrompter
from portkey.wizard.remote_script import (
    FIX_PERMS_SCRIPT,
    KeyLine,
    build_install_script,
    manual_install_command,
    write_failure_reason,
)

if TYPE_CHECKING:
    from portkey.wizard.install import InstallWizard, Outcome, WizardIO

log = logging.getLogger("portkey.wizard")

_KEY_REFUSED: list[WizardAction] = ["install_with_password", "save_password_only", "cancel"]
_CHECK_KEY: list[WizardAction] = ["check_key", "cancel"]
_SAVE_OR_CANCEL: list[WizardAction] = ["save_password_only", "cancel"]
_NOT_A_FAILURE = (PromptCancelled, HostKeyRejected)


def _on_target(exc: SshError) -> bool:
    return exc.hop is not None and exc.hop.role == "target"


class Run:
    def __init__(
        self, wizard: InstallWizard, start: StartMsg, prompter: Prompter, io: WizardIO
    ) -> None:
        self.wizard = wizard
        self.start = start
        self.io = io
        self.reporter = Reporter(io)
        self.steps = StepPrompter(prompter, self.reporter, wizard.hostkey_code)
        self.ref = HopRef(0, 1, start.connection_id, start.name, start.host, start.port, "target")
        self.password_chain: SshChain | None = None
        self.key: KeyRecord | None = None
        self.line: KeyLine | None = None

    async def execute(self) -> tuple[ConnectionRecord, Outcome]:
        try:
            return await self._flow()
        except WsError as exc:
            if not isinstance(exc, _NOT_A_FAILURE):
                await self.reporter.fail_running(exc.code)
            log.info("wizard run ended: %s", exc.code)
            raise
        finally:
            await self._drop_password()

    # --- the flow ---------------------------------------------------------------------------

    async def _flow(self) -> tuple[ConnectionRecord, Outcome]:
        await self._validate()
        assume = self.start.assume_installed
        try:
            chain = await self._connect("first", key=True, secret=not assume, report=True)
        except AuthFailed as exc:
            if not _on_target(exc) or self.steps.secret_started:
                raise
            await self.steps.ensure_hostkey()
            await self.reporter.send("try_key", "warn", code="refused")
            if assume and any(m in SECRET_METHODS for m in exc.methods):
                return await self._key_refused()
            return await self._password_not_allowed()
        if not self.steps.secret_started:
            await _close(chain)
            if assume:
                await self.reporter.send("password_login", "skipped")
            return await self._save("already_installed")
        self.password_chain = chain
        return await self._install()

    async def _validate(self) -> None:
        await self.reporter.send("validate", "running")
        self.key, self.line = await asyncio.to_thread(self.wizard.check_input, self.start)
        await self.reporter.send("validate", "ok")

    async def _key_refused(self) -> tuple[ConnectionRecord, Outcome]:
        key = self._key()
        action = await self._ask(
            "key_refused", _copy.key_refused(self.start.name, key.name), _KEY_REFUSED
        )
        if action == "save_password_only":
            return await self._save("saved_password_only")
        try:
            chain = await self._connect("password", key=False, secret=True, report=False)
        except PasswordNotAllowed as exc:
            if not _on_target(exc):
                raise
            await self.reporter.fail_running(exc.code)
            return await self._password_not_allowed()
        self.password_chain = chain
        return await self._install()

    async def _password_not_allowed(self) -> tuple[ConnectionRecord, Outcome]:
        while True:
            await self._ask("password_not_allowed", _copy.password_not_allowed(self.start.name))
            if await self._check_key():
                return await self._save("already_installed")

    async def _install(self) -> tuple[ConnectionRecord, Outcome]:
        chain = self.password_chain
        assert chain is not None
        await self.reporter.send("install", "running")
        script = build_install_script(self._line().line)
        result = await run_install(chain.target, script, self.ref, self.wizard.script_timeout)
        if result.outcome == "non_posix":
            await self.reporter.send("install", "fail", code="non_posix")
            message = _copy.non_posix(self.start.name, result.output)
            while True:
                await self._ask("non_posix", message, output=result.output)
                if await self._check_key():
                    return await self._save("already_installed")
        if result.outcome in ("readonly", "failed"):
            await self.reporter.send("install", "fail", code="authorized_keys_readonly")
            reason = write_failure_reason(result.output)
            await self._ask(
                "authorized_keys_readonly",
                _copy.authorized_keys_readonly(reason),
                _SAVE_OR_CANCEL,
                output=result.output,
            )
            return await self._save("saved_password_only")
        added = result.outcome == "added"
        await self.reporter.send("install", "ok", code=result.outcome)
        if result.perms_warning:
            await self.reporter.send("install", "warn", code="perms_warning")
        return await self._verify(added)

    async def _verify(self, added: bool) -> tuple[ConnectionRecord, Outcome]:
        fixed = False
        while True:
            await self.reporter.send("verify", "running")
            methods = await self._key_login()
            if methods is None:
                await self.reporter.send("verify", "ok")
                return await self._save("installed")
            await self.reporter.send("verify", "fail", code="key_still_refused")
            key_logins = "publickey" in methods
            actions: list[WizardAction] = list(_SAVE_OR_CANCEL)
            if key_logins and not fixed:
                actions.insert(0, "fix_permissions")
            message = _copy.key_still_refused(
                self.start.name,
                self.start.username,
                added=added,
                key_logins=key_logins,
                fixed=fixed,
            )
            action = await self._ask("key_still_refused", message, actions)
            if action == "save_password_only":
                return await self._save("saved_password_only")
            fixed = True
            chain = self.password_chain
            assert chain is not None
            _, _, status = await run_sh(
                chain.target, FIX_PERMS_SCRIPT, self.ref, self.wizard.script_timeout
            )
            log.info("fix permissions: exit %s", status)

    # --- pieces -----------------------------------------------------------------------------

    async def _check_key(self) -> bool:
        """ "Check key login": a key-only connection. True when the key works."""
        await self.reporter.send("try_key", "running")
        if await self._key_login() is None:
            await self.reporter.send("try_key", "ok", code="already_installed")
            return True
        await self.reporter.send("try_key", "warn", code="refused")
        return False

    async def _key_login(self) -> list[str] | None:
        """Sign in with the key only, run the check command, close. None when it worked,
        otherwise the methods the server offered."""
        try:
            chain = await self._connect("key", key=True, secret=False, report=False)
        except AuthFailed as exc:
            if not _on_target(exc):
                raise
            return list(exc.methods)
        try:
            await run_check(chain.target, self.ref)
        finally:
            await _close(chain)
        return None

    async def _connect(
        self, attempt: Attempt, *, key: bool, secret: bool, report: bool
    ) -> SshChain:
        key_record = self._key()
        target = HopTarget(
            self.ref,
            self.start.username,
            "key" if key else "password",
            key_record.fp if key else None,
            allow_password=secret,
            allow_kbdint=secret,
            allow_key=key,
        )
        hops = await asyncio.to_thread(self.wizard.connector.plan_new, self.start.jump_id, target)
        self.steps.begin(attempt, report_connect=report)
        return await self.wizard.connector.connect(hops, self.steps)

    async def _ask(
        self,
        code: str,
        message: str,
        actions: list[WizardAction] | None = None,
        *,
        output: str | None = None,
    ) -> WizardAction:
        offered = actions or _CHECK_KEY
        line = self._line().line
        msg = NeedsActionMsg.model_validate(
            {
                "code": code,
                "message": message,
                "actions": offered,
                "public_key": line,
                "install_command": manual_install_command(line),
                "output": output or None,
            }
        )
        log.info("needs action: %s", code)
        action = await self.io.needs_action(msg)
        if action == "cancel":
            raise PromptCancelled()
        if action not in offered:
            raise BadMessage()
        return action

    async def _save(self, outcome: Outcome) -> tuple[ConnectionRecord, Outcome]:
        if self.password_chain is not None:
            await self._drop_password()
            await self.reporter.send("discard_password", "ok")
        await self.reporter.send("save", "running")
        record = await asyncio.to_thread(
            self.wizard.save, self.start, outcome, self._key(), self.ref
        )
        await self.reporter.send("save", "ok")
        return record, outcome

    async def _drop_password(self) -> None:
        chain, self.password_chain = self.password_chain, None
        if chain is not None:
            await _close(chain)

    def _key(self) -> KeyRecord:
        assert self.key is not None
        return self.key

    def _line(self) -> KeyLine:
        assert self.line is not None
        return self.line


async def _close(chain: SshChain) -> None:
    with contextlib.suppress(Exception):
        await chain.close()
