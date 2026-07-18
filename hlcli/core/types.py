"""Domain types shared across the CLI, exchange, and executor layers.

Kept deliberately small for Phase 0 — grows as later phases need it. Enums are
str-backed so they serialize cleanly to JSON logs and CLI `--json` output.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

DAY_SECONDS = 86_400.0  # seconds in a UTC day — shared by the daily-count cap and sentry budgets


class Network(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    MAINNET = "mainnet"


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


class Action(StrEnum):
    """LLM per-candidate verdict."""

    ACT = "act"
    SKIP = "skip"


class Timing(StrEnum):
    NOW = "now"
    WAIT = "wait"


class Candidate(BaseModel):
    """A trade setup proposed to the executor (the human-supplied *thesis*)."""

    id: str
    coin: str
    side: Side
    entry: float
    tp: float
    sl: float
    reasoning: str = ""
    news: str = ""
    created_at: float  # unix seconds; used for freshness checks


class Decision(BaseModel):
    """The LLM's judgment on a candidate. An *input* to the gate, never a bypass."""

    candidate_id: str
    action: Action
    timing: Timing
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
    # Minutes to wait before re-checking, when timing is WAIT. The code clamps this
    # into the freshness window; None means "use the code default". Ignored when NOW.
    recheck_in_minutes: float | None = None


class Order(BaseModel):
    """A concrete order the deterministic code intends to place."""

    coin: str
    side: Side
    order_type: OrderType
    size: float
    price: float | None = None  # None for market orders
    reduce_only: bool = False
    trigger_price: float | None = None  # for stop_loss / take_profit
    # Client order id ("0x" + 32 hex = 16 bytes) — lets a transport-unknown submit be
    # resolved against the exchange by client id instead of guessed. Set on the entry
    # (see executor/execute.py); None elsewhere.
    cloid: str | None = None


class Position(BaseModel):
    coin: str
    side: Side
    size: float
    entry_price: float
    unrealized_pnl: float = 0.0


class Candle(BaseModel):
    """One OHLCV bar from the public candleSnapshot feed."""

    t: int  # open time, ms since epoch
    o: float
    h: float
    l: float
    c: float
    v: float


class OpenOrder(BaseModel):
    coin: str
    oid: int
    side: Side
    size: float
    price: float
    order_type: str = "limit"
    reduce_only: bool = False
    is_trigger: bool = False  # a resting SL/TP trigger (native protection)


class OrderResult(BaseModel):
    """Outcome of an exchange write (order / cancel / leverage)."""

    accepted: bool
    status: str = ""
    order_id: str | None = None
    message: str = ""
    # Reconciliation: what actually filled, so the executor opens the ledger and
    # sizes protection against reality — not the intended order. `filled_size` is 0
    # for an accepted-but-resting order; None when the backend doesn't report fills.
    filled_size: float | None = None
    avg_price: float | None = None
