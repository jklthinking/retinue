FROM node:22-bookworm-slim AS webui
WORKDIR /src/webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY webui/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY LICENSE* NOTICE* ./
COPY core ./core
COPY adapters ./adapters
COPY server ./server
COPY node ./node
COPY --from=webui /src/server/static ./server/static
COPY scripts/docker-entrypoint.sh /usr/local/bin/retinue-entrypoint
RUN chmod 0755 /usr/local/bin/retinue-entrypoint \
    && pip install --no-cache-dir '.[server]' \
    && useradd --create-home --uid 10001 retinue \
    && install -d -o retinue -g retinue /data

USER retinue
WORKDIR /data
VOLUME ["/data"]
EXPOSE 9219

ENTRYPOINT ["retinue-entrypoint"]
CMD ["serve", "--host", "0.0.0.0", "--port", "9219"]
