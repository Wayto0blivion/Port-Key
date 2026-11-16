# PLACEHOLDER, owned by package 7: a static page on another origin that tries to open
# ws://app:8000/ws/terminal and reports the result for the e2e suite.
FROM nginx:alpine
RUN printf 'server { listen 8080; root /usr/share/nginx/html; }\n' > /etc/nginx/conf.d/default.conf
