"""MemoryKeyVault: TTL expiry, lock-all, thread safety, no key material in repr."""

from __future__ import annotations

import threading
from typing import Any

import asyncssh
import pytest

from portkey.keys.vault import MemoryKeyVault


@pytest.fixture(scope="module")
def key() -> asyncssh.SSHKey:
    return asyncssh.generate_private_key("ssh-ed25519")


def test_put_get_and_expiry(vault: MemoryKeyVault, clock: Any, key: asyncssh.SSHKey) -> None:
    assert vault.get("SHA256:a") is None
    vault.put("SHA256:a", key, 60)
    assert vault.get("SHA256:a") is key
    assert vault.unlocked() == ["SHA256:a"]
    clock.now += 59.9
    assert vault.get("SHA256:a") is key
    clock.now += 0.1
    assert vault.get("SHA256:a") is None
    assert vault.unlocked() == []


def test_none_ttl_lasts_until_lock_all(
    vault: MemoryKeyVault, clock: Any, key: asyncssh.SSHKey
) -> None:
    vault.put("SHA256:b", key, None)
    vault.put("SHA256:a", key, 10)
    clock.now += 10**9
    assert vault.unlocked() == ["SHA256:b"]
    assert vault.lock_all() == 1
    assert vault.get("SHA256:b") is None
    assert vault.lock_all() == 0


def test_put_again_resets_ttl(vault: MemoryKeyVault, clock: Any, key: asyncssh.SSHKey) -> None:
    vault.put("SHA256:a", key, 10)
    clock.now += 8
    vault.put("SHA256:a", key, 10)
    clock.now += 8
    assert vault.get("SHA256:a") is key


def test_lock_all_counts_live_keys(vault: MemoryKeyVault, clock: Any, key: asyncssh.SSHKey) -> None:
    vault.put("SHA256:a", key, 5)
    vault.put("SHA256:b", key, 50)
    vault.put("SHA256:c", key, 0)
    clock.now += 6
    assert vault.lock_all() == 1
    assert vault.unlocked() == []


def test_repr_has_no_key_material(vault: MemoryKeyVault, key: asyncssh.SSHKey) -> None:
    vault.put("SHA256:a", key, 60)
    text = repr(vault)
    assert text == "<MemoryKeyVault holding 1 key(s)>"
    private = bytes(key.export_private_key("openssh")).decode()
    assert private.splitlines()[1][:20] not in text


def test_default_clock_is_monotonic(key: asyncssh.SSHKey) -> None:
    vault = MemoryKeyVault()
    vault.put("SHA256:a", key, 3600)
    assert vault.get("SHA256:a") is key


def test_concurrent_use(vault: MemoryKeyVault, key: asyncssh.SSHKey) -> None:
    def worker(n: int) -> None:
        for i in range(200):
            vault.put(f"SHA256:{n}-{i % 5}", key, 60)
            vault.get(f"SHA256:{n}-{i % 3}")
            vault.unlocked()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(vault.unlocked()) == 40
    assert vault.lock_all() == 40
