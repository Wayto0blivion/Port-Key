#!/bin/sh
# Install the TEST-ONLY host key set named by HOSTKEY_SET (a or b), then run sshd in the
# foreground, logging to stderr (package 7).
set -eu
set_dir="/etc/ssh/portkey-hostkeys/${HOSTKEY_SET:-a}"
if [ ! -f "$set_dir/ssh_host_ed25519_key" ]; then
    echo "entrypoint: unknown HOSTKEY_SET '${HOSTKEY_SET:-}' (use a or b)" >&2
    exit 1
fi
install -m 600 "$set_dir/ssh_host_ed25519_key" /etc/ssh/ssh_host_ed25519_key
install -m 644 "$set_dir/ssh_host_ed25519_key.pub" /etc/ssh/ssh_host_ed25519_key.pub
/usr/sbin/sshd -t
exec /usr/sbin/sshd -D -e
