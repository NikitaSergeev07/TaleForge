"""TaleForge CLI: ``new``, ``play``, ``load``.

The play loop renders the current scene with rich, prompts ``> ``, and
processes either slash-commands locally or sends free-text through the
:class:`Orchestrator`. Per-turn cost is shown in a footer.

Slash commands handled directly (no LLM):
  /save     snapshot the current state
  /quit     exit (state already saved after each turn)
  /state    pretty-printed WorldState dump (debug)
  /inv      inventory (skips the parser/narrator)
  /undo     restore the previous snapshot from the SQLite save
  /help     list commands
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from .agents.orchestrator import Orchestrator, TurnResult
from .config import Settings, get_settings
from .llm.minimax import MinimaxClient
from .state.store import WorldStateKeeper, _hydrate_state


app = typer.Typer(help="TaleForge — multi-agent text RPG.")
console = Console()


# ── path helpers ──────────────────────────────────────────────────────


def _scenario_path(name: str) -> Path:
    pkg = Path(__file__).resolve().parent / "scenarios"
    for cand in (pkg / f"{name}.yaml", pkg / name, Path(name)):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"scenario {name!r} not found in {pkg} or as a path")


def _saves_dir(settings: Settings) -> Path:
    settings.saves_dir.mkdir(parents=True, exist_ok=True)
    return settings.saves_dir


def _session_db(session_id: str, settings: Settings) -> Path:
    return _saves_dir(settings) / f"{session_id}.sqlite"


def _session_trace(session_id: str, settings: Settings) -> Path:
    settings.traces_dir.mkdir(parents=True, exist_ok=True)
    return settings.traces_dir / f"{session_id}.jsonl"


def _new_session_id(scenario: str) -> str:
    return f"{scenario}-{int(time.time())}"


# ── rich rendering ───────────────────────────────────────────────────


def _print_scene(keeper: WorldStateKeeper) -> None:
    state = keeper.state
    player = state.entities[state.player_id]
    loc = state.locations.get(player.location_id) if player.location_id else None
    title = loc.name if loc else "Nowhere"
    visible = []
    if loc:
        for eid in loc.present_entity_ids:
            if eid != player.id and eid in state.entities:
                visible.append(state.entities[eid].name)
    body_lines = [loc.description if loc else "(no location)"]
    body_lines.append("")
    body_lines.append(f"[bold]Here:[/] {', '.join(visible) if visible else '(no one else)'}")
    if loc and loc.exits:
        body_lines.append(f"[bold]Exits:[/] {', '.join(loc.exits.keys())}")
    body_lines.append(f"[dim]turn {state.turn} · day {state.in_game_time['day']} hour {state.in_game_time['hour']:02d}[/]")
    console.print(Panel("\n".join(body_lines), title=title, border_style="cyan"))


def _format_roll(r: dict) -> str:
    k = r.get("kind")
    if k == "attack":
        verdict = "crit" if r.get("crit") else ("hit" if r.get("success") else "miss")
        return f"atk d20={r['d20']}={r['total']} vs AC{r['dc']} ({verdict})"
    if k == "damage":
        return f"dmg {r['dice']}+{r.get('modifier', 0)}={r['total']}"
    if k == "skill_check":
        verdict = "pass" if r.get("success") else "fail"
        return f"{r['ability']} d20={r['d20']}+{r['modifier']}={r['total']} vs DC{r['dc']} ({verdict})"
    return str(r)


def _print_turn(result: TurnResult, *, show_rolls: bool = False) -> None:
    if result.prose:
        console.print(Panel(result.prose, title=f"Turn {result.turn} · {result.action.intent}", border_style="green"))
    if result.outcome.rolls and show_rolls:
        console.print("[dim italic]rolls: " + "; ".join(_format_roll(r) for r in result.outcome.rolls) + "[/]")
    elif result.outcome.rolls:
        console.print(f"[dim]({len(result.outcome.rolls)} dice rolled — type /rolls to expand)[/]")
    for mut, err in result.rejected_mutations:
        console.print(f"[yellow]rejected:[/] {mut.get('op')} → {err}")
    console.print(f"[dim]cost: ${result.turn_cost_usd:.5f}  ·  total ${result.turn_cost_usd:.5f}+[/]")


# ── undo helper ──────────────────────────────────────────────────────


def _undo_one(keeper: WorldStateKeeper) -> bool:
    if keeper.db_path is None or not keeper.db_path.exists():
        return False
    with sqlite3.connect(keeper.db_path) as conn:
        row = conn.execute(
            "SELECT state_json FROM snapshots WHERE turn < ? ORDER BY turn DESC LIMIT 1",
            (keeper.state.turn,),
        ).fetchone()
    if not row:
        return False
    keeper.state = _hydrate_state(json.loads(row[0]))
    return True


# ── play loop ────────────────────────────────────────────────────────


HELP = (
    "[bold]commands:[/] /save  /quit  /state  /inv  /undo  /rolls  /help"
)


async def _play_loop(session_id: str, db: Path, trace: Path, settings: Settings) -> None:
    keeper = WorldStateKeeper.from_db(db, trace_path=trace)
    show_rolls = False
    last_result: TurnResult | None = None
    async with MinimaxClient(settings=settings) as client:
        orch = Orchestrator(client, keeper, settings=settings)
        console.print(f"[bold cyan]TaleForge[/] · session [bold]{session_id}[/]  ({HELP})\n")
        _print_scene(keeper)

        while True:
            try:
                line = await asyncio.to_thread(console.input, "\n[bold]>[/] ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye.[/]")
                return
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit", "/q"):
                console.print("[dim]bye.[/]")
                return
            if line == "/help":
                console.print(HELP)
                continue
            if line == "/save":
                keeper.save()
                console.print(f"[green]saved at turn {keeper.state.turn}.[/]")
                continue
            if line == "/state":
                console.print(Panel(keeper.state.model_dump_json(indent=2), title="WorldState"))
                continue
            if line in ("/inv", "/inventory"):
                inv = keeper.state.entities[keeper.state.player_id].inventory
                gp = inv.count("gp")
                items = [i for i in inv if i != "gp"] or ["(no gear)"]
                console.print(Panel(", ".join(items) + f"\n[bold]gp:[/] {gp}", title="Inventory"))
                continue
            if line == "/undo":
                ok = _undo_one(keeper)
                console.print(f"[{'green' if ok else 'yellow'}]{'undone' if ok else 'nothing to undo'}.[/]")
                if ok:
                    _print_scene(keeper)
                continue
            if line == "/rolls":
                show_rolls = not show_rolls
                console.print(f"[dim]rolls visible: {show_rolls}[/]")
                if last_result:
                    _print_turn(last_result, show_rolls=show_rolls)
                continue

            try:
                result = await orch.take_turn(line)
            except Exception as e:
                console.print(f"[red]turn failed:[/] {e}")
                continue
            last_result = result
            _print_turn(result, show_rolls=show_rolls)
            keeper.save()


# ── commands ─────────────────────────────────────────────────────────


@app.command()
def new(
    scenario: str = typer.Option("starter_village", help="Scenario name (no .yaml)"),
    session_id: str | None = typer.Option(None, help="Override session id"),
) -> None:
    """Start a new game from a scenario."""
    settings = get_settings()
    spath = _scenario_path(scenario)
    sid = session_id or _new_session_id(scenario)
    db = _session_db(sid, settings)
    trace = _session_trace(sid, settings)
    if db.exists():
        console.print(f"[red]session {sid} already exists at {db}.[/]")
        raise typer.Exit(1)
    keeper = WorldStateKeeper.from_scenario(spath, session_id=sid, db_path=db, trace_path=trace)
    keeper.save()
    console.print(Panel(f"[bold]new session:[/] {sid}\n[dim]{db}[/]", title="taleforge"))
    console.print(f"run: [bold]taleforge play {sid}[/]")


@app.command()
def play(session_id: str) -> None:
    """Resume play in an existing session."""
    settings = get_settings()
    db = _session_db(session_id, settings)
    trace = _session_trace(session_id, settings)
    if not db.exists():
        console.print(f"[red]no session at {db}.[/]")
        raise typer.Exit(1)
    if not settings.minimax_api_key:
        console.print("[red]MINIMAX_API_KEY is not set; cannot play.[/]")
        raise typer.Exit(1)
    asyncio.run(_play_loop(session_id, db, trace, settings))


@app.command()
def load(session_id: str) -> None:
    """Alias for play."""
    play(session_id)


@app.command()
def bench(
    session_id: str | None = typer.Argument(None, help="Session id (default: bench-<ts>)"),
) -> None:
    """Run the consistency benchmark; writes a JSON report under traces/."""
    settings = get_settings()
    if not settings.minimax_api_key:
        console.print("[red]MINIMAX_API_KEY required for bench.[/]")
        raise typer.Exit(1)
    sid = session_id or f"bench-{int(time.time())}"
    db = _session_db(sid, settings)
    if db.exists():
        console.print(f"[red]session {sid} already exists at {db}.[/]")
        raise typer.Exit(1)
    asyncio.run(_run_bench(sid, db, settings))


async def _run_bench(session_id: str, db: Path, settings: Settings) -> None:
    from dataclasses import asdict

    from .bench.consistency import render_bench_report, run_bench

    spath = _scenario_path("starter_village")
    trace = _session_trace(session_id, settings)
    keeper = WorldStateKeeper.from_scenario(
        spath, session_id=session_id, db_path=db, trace_path=trace
    )
    keeper.save()

    async with MinimaxClient(settings=settings) as client:
        orch = Orchestrator(client, keeper, settings=settings)
        console.print(f"[dim]running bench on {session_id} (30 scripted turns)…[/]")
        report = await run_bench(orch, keeper)

    report_path = settings.traces_dir / f"{session_id}_bench.json"
    report_path.write_text(json.dumps(asdict(report), indent=2, default=str))
    console.print(
        Panel(render_bench_report(report), title=f"bench: {session_id}", border_style="magenta")
    )
    console.print(f"[dim]report: {report_path}[/]")


if __name__ == "__main__":
    app()
