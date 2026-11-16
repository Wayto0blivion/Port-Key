"""Settings with defaults and validation, the capped events trail and keys_meta."""

from __future__ import annotations

import json
from typing import Any

import pytest

from portkey.errors import Invalid, NotFound
from portkey.models import AppSettings
from portkey.store.db import Database
from portkey.store.events import SqliteEventLog
from portkey.store.keys_meta import SqliteKeysMetaRepo
from portkey.store.settings import SqliteSettingsRepo

FP_A = "SHA256:" + "A" * 43
FP_B = "SHA256:" + "B" * 43

# --- settings ---------------------------------------------------------------------------------


def test_settings_defaults(settings: SqliteSettingsRepo) -> None:
    assert settings.get() == AppSettings()


def test_settings_update_stores_only_given_keys(settings: SqliteSettingsRepo, db: Database) -> None:
    out = settings.update({"font_size": 16, "default_user": "john", "default_key_fp": FP_A})
    assert (out.font_size, out.default_user, out.default_key_fp) == (16, "john", FP_A)
    assert out.detach_grace == AppSettings().detach_grace
    assert {r[0] for r in db.query("SELECT key FROM settings")} == {
        "font_size",
        "default_user",
        "default_key_fp",
    }
    assert settings.update({"default_key_fp": None}).default_key_fp is None


@pytest.mark.parametrize(
    "change",
    [
        {"font_size": 7},
        {"font_size": 33},
        {"font_size": True},
        {"passphrase_ttl": 59},
        {"passphrase_ttl": 7 * 86400 + 1},
        {"detach_grace": 86401},
        {"default_user": "has space"},
        {"default_key_fp": "SHA256:short"},
        {"windows_copy_paste": "yes"},
        {"leave_warning": None},
        {"theme": "light"},
    ],
)
def test_settings_validation(settings: SqliteSettingsRepo, change: dict[str, Any]) -> None:
    with pytest.raises(Invalid):
        settings.update(change)
    assert settings.get() == AppSettings()


def test_settings_bad_stored_values_fall_back(settings: SqliteSettingsRepo, db: Database) -> None:
    db.execute("INSERT INTO settings VALUES ('font_size', '99')")
    db.execute("INSERT INTO settings VALUES ('obsolete', '1')")
    db.execute("INSERT INTO settings VALUES ('detach_grace', '600')")
    out = settings.get()
    assert out.font_size == AppSettings().font_size
    assert out.detach_grace == 600


# --- events -----------------------------------------------------------------------------------


def test_events_record_and_recent(events: SqliteEventLog) -> None:
    events.record(
        "hostkey_accepted", "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a", host="web-prod", port=22
    )
    events.record("session_opened", None, host="build-box")
    recent = events.recent()
    assert [e.kind for e in recent] == ["session_opened", "hostkey_accepted"]
    assert recent[1].detail == {"host": "web-prod", "port": 22}
    assert recent[1].connection_id == "0b6f3c1e-8a52-4c3e-9d7a-1f2e3d4c5b6a"
    assert len(events.recent(limit=1)) == 1


def test_events_drop_secret_looking_detail(events: SqliteEventLog, db: Database) -> None:
    events.record(
        "key_installed", None, host="web-prod", password="hunter2", passphrase="x", token="t"
    )
    raw = db.query_one("SELECT detail FROM events")[0]
    assert json.loads(raw) == {"host": "web-prod"}
    assert "hunter2" not in raw


def test_events_reject_unknown_kind(events: SqliteEventLog) -> None:
    with pytest.raises(Invalid):
        events.record("made_up")  # type: ignore[arg-type]


def test_events_capped(events: SqliteEventLog, db: Database) -> None:
    with db.tx():
        for i in range(5005):
            events.record("session_opened", None, n=i)
    assert db.query_one("SELECT count(*) FROM events")[0] == 5000
    assert events.recent(1)[0].detail == {"n": 5004}


# --- keys_meta --------------------------------------------------------------------------------


def test_keys_meta_upsert_label_hidden(keys_meta: SqliteKeysMetaRepo) -> None:
    keys_meta.upsert_seen(FP_A, "C:\\Users\\john\\.ssh\\id_ed25519", "ssh_dir")
    first = keys_meta.all()[FP_A]
    assert (first.label, first.hidden, first.source) == (None, False, "ssh_dir")
    keys_meta.upsert_seen(FP_A, "C:\\Users\\john\\.ssh\\port-key\\id_ed25519", "generated")
    keys_meta.set_label(FP_A, "  Work laptop ")
    keys_meta.set_hidden(FP_A, True)
    rec = keys_meta.all()[FP_A]
    assert rec.last_path.endswith("port-key\\id_ed25519")
    assert (rec.label, rec.hidden, rec.source) == ("Work laptop", True, "generated")
    assert rec.created_at == first.created_at
    keys_meta.set_label(FP_A, "  ")
    keys_meta.set_hidden(FP_A, False)
    assert (keys_meta.all()[FP_A].label, keys_meta.all()[FP_A].hidden) == (None, False)


def test_keys_meta_unknown_fp(keys_meta: SqliteKeysMetaRepo) -> None:
    with pytest.raises(NotFound) as err:
        keys_meta.set_hidden(FP_A, True)
    assert err.value.code == "key_unknown"
    with pytest.raises(NotFound):
        keys_meta.set_label(FP_A, "x")
    with pytest.raises(Invalid):
        keys_meta.upsert_seen(FP_A, "/ssh/id_ed25519", "agent")  # type: ignore[arg-type]


def test_hiding_the_default_key_clears_the_default(
    keys_meta: SqliteKeysMetaRepo, settings: SqliteSettingsRepo
) -> None:
    keys_meta.upsert_seen(FP_A, "/ssh/id_ed25519", "ssh_dir")
    keys_meta.upsert_seen(FP_B, "/ssh/id_rsa", "ssh_dir")
    settings.update({"default_key_fp": FP_A})
    keys_meta.set_hidden(FP_B, True)
    assert settings.get().default_key_fp == FP_A
    keys_meta.set_hidden(FP_A, True)
    assert settings.get().default_key_fp is None
