# eslint + tsc --checkJs for portkey/static/js. Versions are locked in package-lock.json.
FROM node:22-bookworm-slim
WORKDIR /opt/lint
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY run.sh ./run.sh
ENV PATH=/opt/lint/node_modules/.bin:$PATH
CMD ["sh", "/opt/lint/run.sh"]
