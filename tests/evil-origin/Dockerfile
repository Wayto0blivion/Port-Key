# TEST-ONLY "malicious site" for the e2e suite (package 7): a static page served from another
# origin (http://evil-origin:8080) that tries to open the app's terminal WebSocket and to send
# it a mutating request. The Origin check must stop both. See tests/evil-origin/index.html.
# Pinned by digest (nginx 1.31.8) so the build does not depend on a moving tag.
FROM nginx:latest@sha256:165ddde8751e75a1c907d24ef10d9f6deb08b57e0fe56caf67512486aff76d82
COPY default.conf /etc/nginx/conf.d/default.conf
COPY index.html evil.js /usr/share/nginx/html/
EXPOSE 8080
