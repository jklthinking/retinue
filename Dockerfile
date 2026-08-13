FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY core ./core
COPY adapters ./adapters
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 retinue \
    && install -d -o retinue -g retinue /data

USER retinue
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8787

ENTRYPOINT ["retinue"]
CMD ["panel", "/data", "--host", "0.0.0.0", "--port", "8787"]
