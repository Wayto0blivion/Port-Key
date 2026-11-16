# TEST-ONLY sshd for Port-Key integration and e2e tests (package 7).
#
# Ubuntu 24.04 + OpenSSH + libpam-google-authenticator + fish, with the users in
# tests/fixtures/testbed.py (pw, keyonly, fresh, tfa, fishy, badperms, nosh). The entrypoint
# picks host key set A or B with HOSTKEY_SET. Built by compose.dev.yaml with the extra build
# context "fixtures" = tests/fixtures/TEST-ONLY/sshd (host keys, users, authorized_keys, TOTP).
FROM ubuntu:24.04

RUN apt-get update \
 && apt-get install --no-install-recommends -y \
        openssh-server libpam-google-authenticator fish \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/ssh/ssh_host_* \
 && mkdir -p /run/sshd \
 # TOTP after the password, for users that have ~/.google_authenticator (nullok: others skip).
 && sed -i 's/^@include common-auth$/@include common-auth\nauth required pam_google_authenticator.so nullok/' \
        /etc/pam.d/sshd \
 # pam_loginuid fails without CAP_AUDIT_CONTROL in a container.
 && sed -i 's/^session\s\+required\s\+pam_loginuid.so/session optional pam_loginuid.so/' \
        /etc/pam.d/sshd \
 && grep -q pam_google_authenticator /etc/pam.d/sshd

COPY --from=fixtures users.tsv keyonly_authorized_keys tfa_google_authenticator /etc/portkey-test/
COPY --from=fixtures --chmod=600 hostkeys/ /etc/ssh/portkey-hostkeys/
COPY sshd_config /etc/ssh/sshd_config
COPY --chmod=755 setup-users.sh /usr/local/sbin/portkey-setup-users
COPY --chmod=755 portkey-test-reset /usr/local/bin/portkey-test-reset
COPY --chmod=755 entrypoint.sh /usr/local/sbin/portkey-sshd-entrypoint
RUN chmod 644 /etc/portkey-test/* \
 && portkey-setup-users

EXPOSE 22 2222
HEALTHCHECK --interval=2s --timeout=2s --start-period=30s --retries=10 \
    CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/22"]
ENTRYPOINT ["/usr/local/sbin/portkey-sshd-entrypoint"]
