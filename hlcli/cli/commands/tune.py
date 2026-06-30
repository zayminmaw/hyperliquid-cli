"""`hl tune` — self-tuning (Mode: out-of-path, propose → approve).

`run` writes proposals (never active); `diff` shows proposed vs live; `promote`
makes proposals active after review; `history` is the audit trail. Both tuners are
sample-gated — on a thin record `run` reports the gate and calls no model.
"""

from __future__ import annotations

import typer

from hlcli.cli.context import state_of
from hlcli.cli.output import emit
from hlcli.core.config import get_caps
from hlcli.core.config_schema import load_tunable
from hlcli.executor.decision import load_decision_prompt
from hlcli.state.store import open_state
from hlcli.tuner.config_tuner import propose_config
from hlcli.tuner.promote import diff as diff_proposals
from hlcli.tuner.promote import history as promotion_history
from hlcli.tuner.promote import promote as promote_proposals
from hlcli.tuner.promote import write_proposed_config, write_proposed_prompt
from hlcli.tuner.prompt_tuner import propose_prompt

app = typer.Typer(no_args_is_help=True, help="Self-tuning: propose → approve.")


@app.command("run")
def run(ctx: typer.Context) -> None:
    """Propose config + prompt edits from the resolved-trade record (writes proposals, never active)."""
    g = state_of(ctx)
    caps = get_caps()
    state = open_state(caps, g.network)

    cfg = propose_config(state, caps, load_tunable())
    prompt = propose_prompt(state, caps, load_decision_prompt(caps))

    written = []
    if cfg.proposed is not None:
        write_proposed_config(caps, cfg.proposed)
        written.append("config")
    if prompt.proposed is not None:
        write_proposed_prompt(caps, prompt.proposed)
        written.append("prompt")

    emit(
        {
            "network": g.network.value,
            "config": cfg.note,
            "prompt": prompt.note,
            "cohorts": [c.key for c in cfg.cohorts],
            "written": written,
            "hint": "review with `tune diff`, then `tune promote`" if written else None,
        },
        as_json=g.json_out, title="tune run",
    )


@app.command("diff")
def diff(ctx: typer.Context) -> None:
    """Show each pending proposal against what's currently live."""
    g = state_of(ctx)
    emit(diff_proposals(get_caps()), as_json=g.json_out, title="tune diff")


@app.command("promote")
def promote(ctx: typer.Context) -> None:
    """Make pending proposals active (re-clamped on the way in)."""
    g = state_of(ctx)
    promoted = promote_proposals(get_caps())
    emit(
        {"promoted": [p["kind"] for p in promoted] or None,
         "note": "nothing to promote" if not promoted else "active"},
        as_json=g.json_out, title="tune promote",
    )


@app.command("history")
def history(ctx: typer.Context) -> None:
    """The promotion audit trail."""
    g = state_of(ctx)
    emit({"promotions": promotion_history(get_caps())}, as_json=g.json_out, title="tune history")
