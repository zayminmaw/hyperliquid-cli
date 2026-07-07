# Running the agent on a VPS

The agent (`hl agent run`) is one foreground process: it watches the intake
directory, runs executor and sentry passes on their cadences, and fires the
daily jobs. It only trades while alive — supervise it with systemd (or Docker's
`--restart=always`) so a crash restarts it. Restarting hard is always safe:
idempotency keys, the intake high-water mark, and ledger-first fills mean a
`kill -9` can never double-fire or re-trade a batch file.

## systemd (recommended)

```bash
# as root, once
useradd -r -m -s /usr/sbin/nologin hl
git clone <your fork> /opt/hyperliquid-cli && cd /opt/hyperliquid-cli
python3.12 -m venv .venv && .venv/bin/pip install ".[exchange,llm]"
cp .env.example .env    # set caps, models, ANTHROPIC_API_KEY, HL_DATA_DIR
chown -R hl:hl /opt/hyperliquid-cli

cp deploy/hl-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hl-agent
journalctl -u hl-agent -f            # live log
```

Check on it any time (any shell, same box):

```bash
sudo -u hl /opt/hyperliquid-cli/.venv/bin/hl --network testnet agent status
```

Structured alerts (fires, rejects, breaker trips, the daily report, heartbeat)
append to `<HL_DATA_DIR>/alerts-<network>.log` as JSON lines — point a webhook
or log shipper at that file for notifications.

## Docker

```bash
docker build -f deploy/Dockerfile -t hl-agent .
docker run -d --name hl-agent --restart=always \
  --env-file .env -v hl-data:/data \
  hl-agent --network testnet -y agent run
```

## Feeding it signals

Drop candidate-batch JSON files into `<HL_DATA_DIR>/intake/<network>/`
(override the base with `HL_AGENT_INTAKE_DIR`). A batch is a list — or a
single object — of candidates:

```json
[{"coin": "BTC", "entry": 60000, "tp": 64000, "sl": 58500,
  "reasoning": "range breakout retest", "news": ""}]
```

Rules for producers:

- **Write atomically**: write to a temp name, then `mv` into the directory.
  The watcher also skips files younger than 2s as a backstop.
- Ingested files move to `processed/`; unparseable ones move to `failed/`
  (with an alert) — nothing is deleted, the raw batch is the audit trail.
- Re-dropping the same content is harmless: candidate ids are content-hashed,
  duplicates are counted and skipped.
- New files trigger an executor pass immediately; freshness still applies
  (`HL_MAX_SIGNAL_AGE_MINUTES`), so stale drops get rejected, not traded.

Cadences (intake poll, exec interval, sentry interval) live in the tunable
surface (`hl config show`); the daily-job time is `HL_AGENT_DAILY_UTC` in `.env`.

## Mainnet

Nothing about agent mode weakens the gate. Mainnet still requires
`HL_ENABLE_MAINNET=1` **and** `--network mainnet` **and** `-y` (in place of the
typed confirmation), and `--manage` additionally requires graduation on the
testnet book. Run testnet until `hl exec report` says graduation is ready.
