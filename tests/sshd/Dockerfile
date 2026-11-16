# PLACEHOLDER, owned by package 7 (test infrastructure).
# Target: Ubuntu 24.04 + OpenSSH + libpam-google-authenticator with the users listed in
# docs/architecture.md section 10 (pw, keyonly, fresh, tfa, fishy, badperms, nosh), host key
# set chosen by HOSTKEY_SET (a or b). Host keys are TEST-ONLY fixtures.
FROM ubuntu:24.04
RUN apt-get update \
 && apt-get install --no-install-recommends -y openssh-server \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/sshd
CMD ["/usr/sbin/sshd", "-D", "-e"]
