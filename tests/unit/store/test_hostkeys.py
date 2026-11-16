"""App-trusted host keys and dismissed known_hosts suggestions."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from portkey.errors import Invalid, NotFound
from portkey.store.db import SCHEMA_V1, SCHEMA_VERSION, Database
from portkey.store.hostkeys import SqliteDismissedSuggestions, SqliteHostKeyStore


def _key(seed: bytes, comment: str = "") -> str:
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + hashlib.sha256(seed).digest()
    text = "ssh-ed25519 " + base64.b64encode(blob).decode()
    return f"{text} {comment}".strip()


def _fp(public_key: str) -> str:
    blob = base64.b64decode(public_key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


KEY_A = _key(b"set-a", "root@web-prod")
KEY_B = _key(b"set-b")


def test_add_strips_comment_lowercases_host_and_fingerprints(host_keys: SqliteHostKeyStore) -> None:
    rec = host_keys.add("Web-Prod ", 22, KEY_A)
    assert rec.host == "web-prod"
    assert rec.key_type == "ssh-ed25519"
    assert rec.public_key == " ".join(KEY_A.split()[:2])
    assert rec.fp_sha256 == _fp(KEY_A)
    assert rec.first_seen_at == rec.last_seen_at
    assert host_keys.get(rec.id) == rec


def test_add_is_idempotent(host_keys: SqliteHostKeyStore) -> None:
    first = host_keys.add("192.168.10.42", 22, KEY_A)
    again = host_keys.add("192.168.10.42", 22, " ".join(KEY_A.split()[:2]) + " other comment")
    assert again.id == first.id
    assert again.first_seen_at == first.first_seen_at
    assert len(host_keys.list_all()) == 1


def test_for_host_matches_host_and_port(host_keys: SqliteHostKeyStore) -> None:
    host_keys.add("imaging-01", 2222, KEY_A)
    host_keys.add("imaging-01", 2222, KEY_B)
    host_keys.add("imaging-01", 22, KEY_B)
    assert len(host_keys.for_host("IMAGING-01", 2222)) == 2
    assert len(host_keys.for_host("imaging-01", 22)) == 1
    assert host_keys.for_host("build-box", 22) == []


def test_replace_drops_every_record_for_host_port(host_keys: SqliteHostKeyStore) -> None:
    host_keys.add("web-prod", 22, KEY_A)
    host_keys.add("web-prod", 22, _key(b"set-a-rsa"))
    other = host_keys.add("build-box", 22, KEY_A)
    new = host_keys.replace("web-prod", 22, KEY_B)
    assert [r.fp_sha256 for r in host_keys.for_host("web-prod", 22)] == [new.fp_sha256]
    assert host_keys.get(other.id) == other


def test_replace_with_bad_key_keeps_old_records(host_keys: SqliteHostKeyStore) -> None:
    host_keys.add("web-prod", 22, KEY_A)
    with pytest.raises(Invalid):
        host_keys.replace("web-prod", 22, "not a key")
    assert len(host_keys.for_host("web-prod", 22)) == 1


@pytest.mark.parametrize("bad", ["", "ssh-ed25519", "ssh-ed25519 !!!notbase64", "ssh-ed25519 ="])
def test_add_rejects_bad_keys(host_keys: SqliteHostKeyStore, bad: str) -> None:
    with pytest.raises(Invalid):
        host_keys.add("web-prod", 22, bad)


def test_add_rejects_bad_host_or_port(host_keys: SqliteHostKeyStore) -> None:
    with pytest.raises(Invalid):
        host_keys.add("  ", 22, KEY_A)
    with pytest.raises(Invalid):
        host_keys.add("web-prod", 0, KEY_A)


def test_touch_and_delete(host_keys: SqliteHostKeyStore) -> None:
    rec = host_keys.add("web-prod", 22, KEY_A)
    host_keys.touch(rec.id)
    assert host_keys.get(rec.id).last_seen_at >= rec.last_seen_at
    deleted = host_keys.delete(rec.id)
    assert deleted.id == rec.id
    with pytest.raises(NotFound):
        host_keys.get(rec.id)
    with pytest.raises(NotFound):
        host_keys.delete(rec.id)


# --- route scope and distrust (review findings) --------------------------------------------------

JUMP_1 = "11111111-1111-4111-8111-111111111111"
JUMP_2 = "22222222-2222-4222-8222-222222222222"


def test_records_are_scoped_by_route(host_keys: SqliteHostKeyStore) -> None:
    # 10.0.0.5 behind two jump hosts is two machines: each route keeps its own key.
    one = host_keys.add("10.0.0.5", 22, KEY_A, JUMP_1)
    two = host_keys.add("10.0.0.5", 22, KEY_B, JUMP_2)
    assert (one.via, two.via) == (JUMP_1, JUMP_2)
    assert [r.id for r in host_keys.for_host("10.0.0.5", 22, JUMP_1)] == [one.id]
    assert [r.id for r in host_keys.for_host("10.0.0.5", 22, JUMP_2)] == [two.id]
    assert host_keys.for_host("10.0.0.5", 22) == []  # direct is a route of its own


def test_replace_leaves_other_routes_alone(host_keys: SqliteHostKeyStore) -> None:
    one = host_keys.add("10.0.0.5", 22, KEY_A, JUMP_1)
    new = host_keys.replace("10.0.0.5", 22, KEY_B, JUMP_2)
    assert host_keys.get(one.id) == one
    assert [r.id for r in host_keys.for_host("10.0.0.5", 22, JUMP_2)] == [new.id]


def test_replace_distrusts_superseded_known_hosts_keys(host_keys: SqliteHostKeyStore) -> None:
    new = host_keys.replace("web-prod", 22, KEY_B, supersede=[KEY_A])
    records = {r.public_key: r for r in host_keys.for_host("web-prod", 22)}
    assert set(records) == {" ".join(KEY_A.split()[:2]), new.public_key}
    assert records[" ".join(KEY_A.split()[:2])].distrusted
    assert not records[new.public_key].distrusted
    # A later replacement keeps the distrust; trusting that key again clears it.
    host_keys.replace("web-prod", 22, _key(b"set-c"))
    assert [r.distrusted for r in host_keys.for_host("web-prod", 22)] == [True, False]
    again = host_keys.add("web-prod", 22, KEY_A)
    assert not again.distrusted


def test_replace_with_a_bad_superseded_key_changes_nothing(
    host_keys: SqliteHostKeyStore,
) -> None:
    host_keys.add("web-prod", 22, KEY_A)
    with pytest.raises(Invalid):
        host_keys.replace("web-prod", 22, KEY_B, supersede=["junk"])
    assert [r.fp_sha256 for r in host_keys.for_host("web-prod", 22)] == [_fp(KEY_A)]


def test_version_1_records_become_direct_ones(tmp_path: Path) -> None:
    db = Database(tmp_path / "v1.db")
    db.open()
    db.conn.executescript(f"BEGIN; {SCHEMA_V1} PRAGMA user_version = 1; COMMIT;")
    db.execute(
        "INSERT INTO host_keys (host, port, key_type, public_key, fp_sha256, first_seen_at,"
        " last_seen_at) VALUES ('web-prod', 22, 'ssh-ed25519', ?, ?, ?, ?)",
        (" ".join(KEY_A.split()[:2]), _fp(KEY_A), "2026-03-02T10:00:00Z", "2026-11-15T14:02:00Z"),
    )
    assert db.migrate() == SCHEMA_VERSION
    store = SqliteHostKeyStore(db)
    [old] = store.for_host("web-prod", 22)
    assert (old.id, old.via, old.distrusted, old.fp_sha256) == (1, None, False, _fp(KEY_A))
    assert old.first_seen_at.isoformat() == "2026-03-02T10:00:00+00:00"
    assert store.add("web-prod", 22, KEY_B).id == 2  # ids keep counting up
    db.close()


def test_dismissed_suggestions(dismissed: SqliteDismissedSuggestions) -> None:
    dismissed.dismiss("Parser-VM", 22)
    dismissed.dismiss("parser-vm", 22)
    dismissed.dismiss("imaging-01", 2222)
    assert dismissed.list_all() == {("parser-vm", 22), ("imaging-01", 2222)}
    dismissed.restore("PARSER-VM", 22)
    dismissed.restore("parser-vm", 22)
    assert dismissed.list_all() == {("imaging-01", 2222)}
