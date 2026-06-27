"""Domain types shared across the CLI, exchange, and executor layers.

Kept deliberately small for Phase 0 — grows as later phases need it. Enums are
str-backed so they serialize cleanly to JSON logs and CLI `--json` output.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


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


class Order(BaseModel):
    """A concrete order the deterministic code intends to place."""

    coin: str
    side: Side
    order_type: OrderType
    size: float
    price: float | None = None  # None for market orders
    reduce_only: bool = False
    trigger_price: float | None = None  # for stop_loss / take_profit


class Position(BaseModel):
    coin: str
    side: Side
    size: float
    entry_price: float
    unrealized_pnl: float = 0.0
