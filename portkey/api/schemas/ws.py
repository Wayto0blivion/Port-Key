"""WebSocket messages for /ws/terminal and /ws/install (docs/ws-protocol.md).

Text frames carry one JSON object with a ``type``. Binary frames carry terminal bytes and have
no schema. ``parse_client_message`` validates an incoming text frame; server messages are built
from the models below and sent with ``api.ws_common.send``, which leaves out optional fields
that are None but keeps required nullable ones as ``null`` (``first_seen_at``,
``Hop.connection_id``, ``exit_status``, ``signal``), as docs/ws-protocol.md says.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

from portkey.api.schemas.common import (
    Color,
    ConnName,
    Fingerprint,
    Hostname,
    Notes,
    Port,
    Response,
    TagNames,
    Username,
    Uuid,
)
from portkey.api.schemas.connections import Connection


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- client -> server ---------------------------------------------------------------------------


class AuthMsg(_In):
    type: Literal["auth"]
    token: Annotated[str, Field(min_length=1, max_length=256)]


class OpenMsg(_In):
    type: Literal["open"]
    connection_id: Uuid
    cols: int
    rows: int
    force_password: bool = False  # "Sign in with password this time"


class AttachMsg(_In):
    type: Literal["attach"]
    session_id: str
    since: Annotated[int, Field(ge=0)] | None  # bytes already received; null = full replay
    cols: int | None = None
    rows: int | None = None


class ResizeMsg(_In):
    type: Literal["resize"]
    cols: int  # clamped by the server to 1..1000
    rows: int


class PingMsg(_In):
    type: Literal["ping"]


class CloseMsg(_In):
    type: Literal["close"]


class PromptReplyMsg(_In):
    """Answer to a ``prompt``. Which fields matter depends on the prompt's kind."""

    type: Literal["prompt_reply"]
    id: str
    cancel: bool = False
    accept: bool | None = None  # hostkey_new, hostkey_changed
    confirm_name: str | None = None  # hostkey_changed
    value: SecretStr | None = None  # passphrase, password
    remember: bool | None = None  # passphrase
    values: list[SecretStr] | None = None  # kbdint


class StartMsg(_In):
    """/ws/install only."""

    type: Literal["start"]
    host: Hostname
    port: Port = 22
    username: Username
    key_fp: Fingerprint | None = None  # null = the default key
    name: ConnName
    jump_id: Uuid | None = None
    group_id: Uuid | None = None
    tags: TagNames = Field(default_factory=list)
    color: Color = "grey"
    pinned: bool = False
    notes: Notes = ""
    assume_installed: bool = False  # "My key is already on this host": skip the password step
    connection_id: Uuid | None = None  # update this saved connection instead of creating one


WizardAction = Literal[
    "install_with_password", "check_key", "fix_permissions", "save_password_only", "cancel"
]


class ActionMsg(_In):
    """/ws/install only: the user's choice after a ``needs_action``."""

    type: Literal["action"]
    action: WizardAction


ClientMessage = Annotated[
    AuthMsg
    | OpenMsg
    | AttachMsg
    | ResizeMsg
    | PingMsg
    | CloseMsg
    | PromptReplyMsg
    | StartMsg
    | ActionMsg,
    Field(discriminator="type"),
]
_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(text: str | bytes) -> ClientMessage:
    """Validate one text frame. Raises ``pydantic.ValidationError`` (send ``bad_message``)."""
    return _client_adapter.validate_json(text)


# --- server -> client ---------------------------------------------------------------------------


class Hop(Response):
    index: int  # 0 = outermost jump host; count - 1 = the target
    count: int
    connection_id: str | None  # null for a wizard target that is not saved yet
    name: str
    host: str
    port: int
    role: Literal["jump", "target"]


Phase = Literal["connecting", "hostkey", "auth", "ready"]


class StatusMsg(Response):
    type: Literal["status"] = "status"
    phase: Phase
    hop: Hop
    method: Literal["publickey", "password", "keyboard-interactive"] | None = None
    key_name: str | None = None  # with method publickey


class _Prompt(Response):
    type: Literal["prompt"] = "prompt"
    id: str
    hop: Hop


class HostKeyNewPrompt(_Prompt):
    kind: Literal["hostkey_new"] = "hostkey_new"
    host: str
    port: int
    key_type: str
    fingerprint: str


class HostKeyChangedPrompt(_Prompt):
    kind: Literal["hostkey_changed"] = "hostkey_changed"
    host: str
    port: int
    key_type: str
    old_key_type: str
    old_fingerprint: str
    new_fingerprint: str
    first_seen_at: datetime | None  # null when the old key is from the owner's known_hosts
    confirm_name: str  # the name the user must type (the hop's connection name)


class PassphrasePrompt(_Prompt):
    kind: Literal["passphrase"] = "passphrase"
    key_fp: str
    key_name: str
    path: str  # Windows path
    retry: bool


class PasswordPrompt(_Prompt):
    kind: Literal["password"] = "password"
    user: str
    host: str
    port: int
    retry: bool


class KbdintQuestion(Response):
    text: str
    echo: bool


class KbdintPrompt(_Prompt):
    kind: Literal["kbdint"] = "kbdint"
    name: str
    instructions: str
    prompts: list[KbdintQuestion]
    retry: bool = False


PromptMsg = Annotated[
    HostKeyNewPrompt | HostKeyChangedPrompt | PassphrasePrompt | PasswordPrompt | KbdintPrompt,
    Field(discriminator="kind"),
]


class ReadyMsg(Response):
    type: Literal["ready"] = "ready"
    session_id: str
    connection: Connection
    offset: int  # absolute byte offset of the first output byte that follows
    reset: bool  # clear the terminal before writing (full replay, or bytes were lost)
    reattached: bool


class ExitMsg(Response):
    type: Literal["exit"] = "exit"
    exit_status: int | None
    signal: str | None
    reason: Literal["closed", "lost", "killed"]  # remote closed / keepalive lost / closed by user


class DetachedMsg(Response):
    type: Literal["detached"] = "detached"
    reason: Literal["attached_elsewhere"] = "attached_elsewhere"


class PongMsg(Response):
    type: Literal["pong"] = "pong"


class ErrorMsg(Response):
    type: Literal["error"] = "error"
    code: str  # portkey.ssh.errors.WsErrorCode
    message: str
    close: int  # the close code that follows
    hop: Hop | None = None
    methods: list[str] | None = None  # auth_failed: what the server offered
    cause: str | None = None  # jump_failed: the hop's own error code


WizardStep = Literal[
    "validate",
    "connect",
    "hostkey",
    "try_key",
    "password_login",
    "install",
    "verify",
    "discard_password",
    "save",
]


class StepMsg(Response):
    type: Literal["step"] = "step"
    step: WizardStep
    state: Literal["running", "ok", "warn", "fail", "skipped"]
    code: str | None = None  # see docs/ws-protocol.md "Step codes"
    detail: str | None = None
    hop: Hop | None = None


class NeedsActionMsg(Response):
    type: Literal["needs_action"] = "needs_action"
    code: Literal[
        "key_refused",
        "password_not_allowed",
        "key_still_refused",
        "authorized_keys_readonly",
        "non_posix",
    ]
    message: str
    actions: list[WizardAction]
    public_key: str | None = None
    install_command: str | None = None
    output: str | None = None  # remote output for the collapsed "Details" block (max 4 KB)


class DoneMsg(Response):
    type: Literal["done"] = "done"
    connection: Connection
    outcome: Literal["installed", "already_installed", "saved_password_only"]


ServerMessage = (
    StatusMsg
    | HostKeyNewPrompt
    | HostKeyChangedPrompt
    | PassphrasePrompt
    | PasswordPrompt
    | KbdintPrompt
    | ReadyMsg
    | ExitMsg
    | DetachedMsg
    | PongMsg
    | ErrorMsg
    | StepMsg
    | NeedsActionMsg
    | DoneMsg
)
