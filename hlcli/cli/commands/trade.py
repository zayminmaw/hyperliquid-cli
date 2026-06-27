"""`hl trade` — manual (Mode A) orders.

No LLM, no risk gate: just the hard caps (allowed-coin, notional, leverage) plus
the exchange's own validation. This is the human-in-control path for discretionary
trades and for manually closing what the executor opened.
"""

from __future__ import annotations

import typer

from hlcli.cli.context import GlobalState, build_for, state_of
from hlcli.cli.output import emit, note
from hlcli.core.config import Caps, get_caps
from hlcli.core.types import Order, OrderType, Side

app = typer.Typer(no_args_is_help=True, help="Manual (Mode A) trading.")
order_app = typer.Typer(no_args_is_help=True, help="Place an order: limit | market | stop-loss | take-profit.")
app.add_typer(order_app, name="order")


def _enforce_allowed_coin(coin: str, caps: Caps) -> None:
    if coin not in caps.coins:
        raise typer.BadParameter(f"{coin} is not in ALLOWED_COINS ({', '.join(caps.coins)}).")


def _enforce_notional(size: float, price: float, caps: Caps) -> None:
    notional = size * price
    if notional > caps.max_notional_per_trade:
        raise typer.BadParameter(
            f"notional ${notional:,.2f} exceeds MAX_NOTIONAL_PER_TRADE ${caps.max_notional_per_trade:,.2f}."
        )


def _submit(state: GlobalState, order: Order) -> None:
    caps = get_caps()
    _enforce_allowed_coin(order.coin, caps)

    exchange = build_for(state, for_write=True)
    ref_price = order.price or order.trigger_price or exchange.get_marks().get(order.coin)
    if ref_price is None:
        raise typer.BadParameter(f"no mark available for {order.coin}.")
    _enforce_notional(order.size, ref_price, caps)

    payload = {
        "coin": order.coin, "side": order.side.value, "type": order.order_type.value,
        "size": order.size, "price": order.price, "trigger": order.trigger_price,
        "reduce_only": order.reduce_only,
    }
    if state.dry_run:
        emit({**payload, "dry_run": True}, as_json=state.json_out, title="trade (dry-run)")
        return

    result = exchange.place_order(order)
    emit({**payload, **result.model_dump()}, as_json=state.json_out, title="order")
    if not result.accepted:
        raise typer.Exit(1)


@order_app.command("limit")
def limit(
    ctx: typer.Context,
    coin: str = typer.Argument(...),
    side: Side = typer.Argument(..., help="long | short"),
    size: float = typer.Argument(...),
    price: float = typer.Argument(...),
    reduce_only: bool = typer.Option(False, "--reduce-only"),
) -> None:
    _submit(state_of(ctx), Order(
        coin=coin.upper(), side=side, order_type=OrderType.LIMIT,
        size=size, price=price, reduce_only=reduce_only,
    ))


@order_app.command("market")
def market(
    ctx: typer.Context,
    coin: str = typer.Argument(...),
    side: Side = typer.Argument(..., help="long | short"),
    size: float = typer.Argument(...),
    reduce_only: bool = typer.Option(False, "--reduce-only"),
) -> None:
    _submit(state_of(ctx), Order(
        coin=coin.upper(), side=side, order_type=OrderType.MARKET,
        size=size, reduce_only=reduce_only,
    ))


@order_app.command("stop-loss")
def stop_loss(
    ctx: typer.Context,
    coin: str = typer.Argument(...),
    side: Side = typer.Argument(..., help="closing side: short to protect a long"),
    size: float = typer.Argument(...),
    trigger: float = typer.Argument(..., help="trigger price"),
    reduce_only: bool = typer.Option(True, "--reduce-only/--no-reduce-only"),
) -> None:
    _submit(state_of(ctx), Order(
        coin=coin.upper(), side=side, order_type=OrderType.STOP_LOSS,
        size=size, trigger_price=trigger, reduce_only=reduce_only,
    ))


@order_app.command("take-profit")
def take_profit(
    ctx: typer.Context,
    coin: str = typer.Argument(...),
    side: Side = typer.Argument(..., help="closing side: short to protect a long"),
    size: float = typer.Argument(...),
    trigger: float = typer.Argument(..., help="trigger price"),
    reduce_only: bool = typer.Option(True, "--reduce-only/--no-reduce-only"),
) -> None:
    _submit(state_of(ctx), Order(
        coin=coin.upper(), side=side, order_type=OrderType.TAKE_PROFIT,
        size=size, trigger_price=trigger, reduce_only=reduce_only,
    ))


@app.command("cancel")
def cancel(ctx: typer.Context, coin: str = typer.Argument(...), oid: int = typer.Argument(...)) -> None:
    state = state_of(ctx)
    result = build_for(state, for_write=True).cancel(coin.upper(), oid)
    emit(result.model_dump(), as_json=state.json_out, title="cancel")
    if not result.accepted:
        raise typer.Exit(1)


@app.command("cancel-all")
def cancel_all(ctx: typer.Context, coin: str = typer.Option(None, "--coin", help="limit to one coin")) -> None:
    state = state_of(ctx)
    count = build_for(state, for_write=True).cancel_all(coin.upper() if coin else None)
    note(f"canceled [yellow]{count}[/yellow] order(s)")


@app.command("set-leverage")
def set_leverage(
    ctx: typer.Context,
    coin: str = typer.Argument(...),
    leverage: int = typer.Argument(...),
    isolated: bool = typer.Option(False, "--isolated", help="isolated margin (default cross)"),
) -> None:
    state = state_of(ctx)
    caps = get_caps()
    if leverage > caps.max_leverage:
        raise typer.BadParameter(f"leverage {leverage}x exceeds MAX_LEVERAGE {caps.max_leverage}x.")
    result = build_for(state, for_write=True).set_leverage(coin.upper(), leverage, cross=not isolated)
    emit(result.model_dump(), as_json=state.json_out, title="set-leverage")
    if not result.accepted:
        raise typer.Exit(1)
