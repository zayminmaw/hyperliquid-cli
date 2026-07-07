# hl agent container (PLAN.md §15). Build from the repo root:
#   docker build -f deploy/Dockerfile -t hl-agent .
# Run with state on a volume and caps via env file:
#   docker run -d --restart=always --env-file .env -v hl-data:/data hl-agent
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md* ./
COPY hlcli ./hlcli
# exchange extra = live testnet/mainnet signing; llm extra = the decision/tuner calls.
RUN pip install --no-cache-dir ".[exchange,llm]"

# Everything mutable (accounts db, keys, state, intake dir, alerts) lives under /data.
ENV HL_DATA_DIR=/data
VOLUME /data

ENTRYPOINT ["hl"]
CMD ["agent", "run"]
