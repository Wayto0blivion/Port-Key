"""``status hostkey`` says where an already-trusted host key came from (``_hop._hostkey_source``).

The wizard turns it into the ``hostkey`` step's code (``known`` / ``trusted``) instead of
guessing from the stored records afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime

from portkey.models import HostKeyRecord, KnownHostsMatch
from portkey.ssh import hostkey
from portkey.ssh._hop import _hostkey_source
from tests.fixtures.testbed import fingerprint
from tests.unit.ssh.fakes import host_key

KEY = host_key("presented")
OTHER = host_key("other")
NOW = datetime(2026, 11, 16, tzinfo=UTC)


def record(key: str) -> HostKeyRecord:
    return HostKeyRecord(
        1, "web-prod.example.test", 22, "ssh-ed25519", key, fingerprint(key), NOW, NOW
    )


def source(known: KnownHostsMatch, stored: list[HostKeyRecord]) -> dict[str, str]:
    return _hostkey_source(hostkey.decide(KEY, known, stored))


def test_known_hosts_entry() -> None:
    assert source(KnownHostsMatch(trusted=(KEY,)), []) == {"source": "known_hosts"}


def test_known_hosts_wins_when_both_hold_the_key() -> None:
    assert source(KnownHostsMatch(trusted=(KEY,)), [record(KEY)]) == {"source": "known_hosts"}


def test_app_store_record() -> None:
    assert source(KnownHostsMatch(trusted=(OTHER,)), [record(KEY)]) == {"source": "store"}


def test_new_and_changed_keys_carry_no_source() -> None:
    assert source(KnownHostsMatch(), []) == {}
    assert source(KnownHostsMatch(trusted=(OTHER,)), [record(OTHER)]) == {}
