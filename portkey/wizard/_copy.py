"""UX copy for the wizard's ``needs_action`` messages (docs/ux-spec.md "Failures").

Messages name the connection, the user and the key only. Remote output never goes into a
message; it travels separately in ``needs_action.output`` for the collapsed Details block.
"""

from __future__ import annotations

WINDOWS_ADMIN_KEYS = "C:\\ProgramData\\ssh\\administrators_authorized_keys"
_WINDOWS_HINTS = (
    "is not recognized as an internal or external command",
    "commandnotfoundexception",
    "is not recognized as the name of a cmdlet",
)


def key_refused(name: str, key_name: str) -> str:
    return (
        f"Key login was refused. {name} did not accept {key_name}. Install it with a password, "
        "or save the connection as password-only."
    )


def password_not_allowed(name: str) -> str:
    return (
        f"{name} does not accept passwords. The server only allows key logins, so Port-Key "
        "can't install your key for you. Add the key on the host yourself, for example from "
        "its console, then check again."
    )


def key_still_refused(name: str, user: str, *, added: bool, key_logins: bool, fixed: bool) -> str:
    if not key_logins:
        return (
            f"Your key is in ~/.ssh/authorized_keys, but {name} does not allow key logins for "
            f"{user}. Its sshd settings (PubkeyAuthentication or AuthenticationMethods) only "
            "accept passwords."
        )
    if fixed:
        return (
            f"{name} still refused your key after Port-Key fixed the permissions. Check the "
            "server's sshd settings: a custom AuthorizedKeysFile, PubkeyAuthentication no, or "
            "AuthenticationMethods that also require a password."
        )
    lead = "Port-Key added your key" if added else "Your key is in ~/.ssh/authorized_keys"
    return (
        f"{lead}, but {name} still refused it. The usual cause is permissions: sshd ignores "
        "authorized_keys if other users can write to it or to ~/.ssh."
    )


def authorized_keys_readonly(reason: str | None) -> str:
    because = f" ({reason})" if reason else ""
    return (
        f"Port-Key signed in but couldn't write ~/.ssh/authorized_keys{because}. Some "
        "appliances store SSH keys elsewhere. Check the device's documentation for how to add "
        "a key."
    )


def non_posix(name: str, output: str) -> str:
    text = (
        f"{name} doesn't offer a standard shell, so Port-Key can't install the key for you. "
        "Add the public key on the host yourself, then select Check key login."
    )
    lowered = output.lower()
    if any(hint in lowered for hint in _WINDOWS_HINTS):
        text += f" On Windows, an administrator account reads its keys from {WINDOWS_ADMIN_KEYS}."
    return text
