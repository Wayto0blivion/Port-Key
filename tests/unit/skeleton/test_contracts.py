"""Contract helpers that package 0 implements: config, fingerprint ids, message parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from portkey.api.schemas import ConnectionCreate, ConnectionUpdate, fp_from_id, fp_to_id
from portkey.api.schemas.ws import (
    AttachMsg,
    ErrorMsg,
    OpenMsg,
    PromptReplyMsg,
    parse_client_message,
)
from portkey.config import load_config
from portkey.errors import Conflict
from portkey.ssh.errors import CloseCode, HostKeyChanged, close_code_for

FP = "SHA256:pZ8mQ1c+Tw4E/Xk3fP0r9QaZ8mQ1c+Tw4E/Xk3fP0r9"


def test_load_config_defaults_and_display_paths() -> None:
    c = load_config({"PORTKEY_SSH_DIR_DISPLAY": "C:\\Users\\john/.ssh"})
    assert c.ssh_dir_display == "C:\\Users\\john\\.ssh"
    assert c.keys_dir_display == "C:\\Users\\john\\.ssh\\port-key"
    assert c.allowed_hosts == ("localhost:8822", "127.0.0.1:8822", "[::1]:8822")
    assert c.allowed_origins[0] == "http://localhost:8822"
    assert str(c.db_path) == "/data/portkey.db"


def test_allowed_hosts_override() -> None:
    c = load_config({"PORTKEY_ALLOWED_HOSTS": "App:8000, other:1"})
    assert c.allowed_hosts == ("app:8000", "other:1")


def test_fp_id_round_trip() -> None:
    key_id = fp_to_id(FP)
    assert not set("/+:") & set(key_id)
    assert fp_from_id(key_id) == FP
    assert fp_from_id("../etc/passwd") is None


def test_parse_client_messages() -> None:
    msg = parse_client_message(
        '{"type":"open","connection_id":"0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a",'
        '"cols":120,"rows":40}'
    )
    assert isinstance(msg, OpenMsg)
    assert not msg.force_password
    msg = parse_client_message('{"type":"attach","session_id":"s1","since":null}')
    assert isinstance(msg, AttachMsg)
    assert msg.since is None
    reply = parse_client_message('{"type":"prompt_reply","id":"p1","value":"hunter2"}')
    assert isinstance(reply, PromptReplyMsg)
    assert "hunter2" not in repr(reply)  # secrets never show in reprs or logs
    with pytest.raises(ValidationError):
        parse_client_message('{"type":"open","connection_id":"x","cols":1,"rows":1,"evil":1}')
    with pytest.raises(ValidationError):
        parse_client_message('{"type":"nope"}')


def test_error_message_shape() -> None:
    msg = ErrorMsg(code="hostkey_changed", message="Changed.", close=4409)
    assert msg.model_dump(exclude_none=True) == {
        "type": "error",
        "code": "hostkey_changed",
        "message": "Changed.",
        "close": 4409,
    }


def test_tags_normalised_and_validated() -> None:
    body = ConnectionCreate(
        name="web-prod",
        host="192.168.10.42",
        username="john",
        auth_method="key",
        tags=[" Prod ", "office-lan"],
    )
    assert body.tags == ["prod", "office-lan"]
    with pytest.raises(ValidationError):
        ConnectionUpdate(tags=["no spaces"])
    with pytest.raises(ValidationError):
        ConnectionUpdate(tags=["-leading-dash"])


def test_update_distinguishes_null_from_absent() -> None:
    body = ConnectionUpdate.model_validate({"group_id": None})
    assert body.model_dump(exclude_unset=True) == {"group_id": None}


def test_error_classes() -> None:
    exc = Conflict("Name taken.", code="name_taken", dependants=None)
    assert exc.status == 409
    assert exc.to_body() == {"code": "name_taken", "message": "Name taken."}
    assert close_code_for(HostKeyChanged()) == CloseCode.HOSTKEY_CHANGED == 4409
    assert close_code_for(ValueError()) == 1011
