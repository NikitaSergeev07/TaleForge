"""Consistency benchmark.

Run a 30-action script through the orchestrator, then ask the Narrator (in a
chronicler framing — given only its prose history, no state) ten factual
questions whose ground truth is read directly from :class:`WorldState`. The
report scores the Narrator's recall and counts mutation rejections.

Saved alongside the JSONL trace as ``traces/<session_id>_bench.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..agents.orchestrator import Orchestrator
from ..llm.minimax import MinimaxClient
from ..models import WorldState
from ..state.store import WorldStateKeeper


# ── disposition label (mirrors npc_actor's; copied to avoid private import) ──


def _disposition_label(d: int) -> str:
    if d <= -75: return "loathing"
    if d <= -40: return "hostile"
    if d <= -10: return "wary"
    if d <= 10:  return "neutral"
    if d <= 40:  return "friendly"
    if d <= 75:  return "warm"
    return "devoted"


# ── 30-action script (combat, dialogue, movement, item use) ─────────────────


SCRIPTED_30: list[str] = [
    "look around",                                    # 1
    "go north",                                       # 2  square → tavern
    "say hi to Maren",                                # 3
    "ask Maren about lodging",                        # 4
    "ask Maren about the wolves",                     # 5
    "go south",                                       # 6  tavern → square
    "go east",                                        # 7  square → smithy
    "say hi to Elder Roan",                           # 8
    "ask Roan about the bounty",                      # 9
    "ask Roan about the wolves' true nature",         # 10
    "go west",                                        # 11 smithy → square
    "say hi to Tibor",                                # 12
    "ask Tibor to join my adventure",                 # 13
    "ask Tibor about the woods",                      # 14
    "check inventory",                                # 15
    "go west",                                        # 16 square → edge_of_woods
    "search for wolf tracks",                         # 17
    "look around",                                    # 18
    "go west",                                        # 19 edge_of_woods → deep_woods
    "listen for any movement",                        # 20
    "go north",                                       # 21 deep_woods → wolf_den
    "attack the dire wolf",                           # 22
    "attack the dire wolf again",                     # 23
    "attack the dire wolf again",                     # 24
    "attack the dire wolf one more time",             # 25
    "go south",                                       # 26 wolf_den → deep_woods
    "go east",                                        # 27 deep_woods → edge_of_woods
    "go east",                                        # 28 edge_of_woods → square
    "go east",                                        # 29 square → smithy
    "tell Elder Roan the wolf is dead",               # 30
]


# ── 10 fact questions ──────────────────────────────────────────────────────


@dataclass
class FactQuestion:
    id: str
    question: str
    truth_fn: Callable[[WorldState], Any]
    grade_fn: Callable[[Any, str], bool]


def _yn_alive(t: bool, a: str) -> bool:
    if t:
        return any(w in a for w in ("yes", "alive", "still", "lives", "breathing"))
    return any(w in a for w in ("no,", "no.", "dead", "killed", "fallen", "down", "slain"))


FACT_QUESTIONS: list[FactQuestion] = [
    FactQuestion(
        "wolf_alive", "Is the dire wolf still alive?",
        lambda s: s.entities["dire_wolf"].alive, _yn_alive,
    ),
    FactQuestion(
        "tibor_alive", "Is Tibor the bard still alive?",
        lambda s: s.entities["tibor"].alive, _yn_alive,
    ),
    FactQuestion(
        "player_location", "What location is the player in right now?",
        lambda s: s.locations[s.entities[s.player_id].location_id].name,
        lambda t, a: any(w in a for w in t.lower().split()),
    ),
    FactQuestion(
        "player_gp", "How many gold pieces (gp) does the player have? Reply with just the number.",
        lambda s: s.entities[s.player_id].inventory.count("gp"),
        lambda t, a: str(t) in a,
    ),
    FactQuestion(
        "player_hp", "What is the player's current HP? Reply with just the number.",
        lambda s: s.entities[s.player_id].hp,
        lambda t, a: str(t) in a,
    ),
    FactQuestion(
        "tibor_disposition", "How does Tibor regard the player? One word: loathing/hostile/wary/neutral/friendly/warm/devoted.",
        lambda s: _disposition_label(s.entities["tibor"].disposition),
        lambda t, a: t.lower() in a,
    ),
    FactQuestion(
        "maren_disposition", "How does Maren regard the player? One word: loathing/hostile/wary/neutral/friendly/warm/devoted.",
        lambda s: _disposition_label(s.entities["maren"].disposition),
        lambda t, a: t.lower() in a,
    ),
    FactQuestion(
        "quest_state", "What is the status of the Howling Woods quest? Reply with one word: active, completed, or failed.",
        lambda s: s.quests["howling_woods"].state,
        lambda t, a: t in a,
    ),
    FactQuestion(
        "tibor_memory", "Has Tibor learned anything about the player this session? Yes or no.",
        lambda s: bool(s.entities["tibor"].memory),
        lambda t, a: ("yes" in a or "learned" in a) if t else any(w in a for w in ("no,", "no.", "nothing", "unknown")),
    ),
    FactQuestion(
        "game_day", "What in-game day is it? Reply with a single number.",
        lambda s: int(s.in_game_time.get("day", 1)),
        lambda t, a: str(t) in a,
    ),
]


# ── chronicler prompt: ask narrator using only its prose history ────────


CHRONICLER = """You are an in-character chronicler of a D&D-flavored solo
adventure. The user gives you a transcript of the prose so far and a single
factual question. Answer using ONLY the facts present in the transcript. If
the transcript does not contain the answer, reply "unknown". Keep the answer
to one short sentence.
"""


async def _ask_chronicler(
    client: MinimaxClient, model: str, prose_history: list[str], question: str
) -> str:
    transcript = "\n\n".join(prose_history) or "(no narrative yet)"
    result = await client.chat(
        [
            {"role": "system", "content": CHRONICLER},
            {"role": "user", "content": f"NARRATIVE:\n{transcript}\n\nQUESTION: {question}"},
        ],
        model=model,
        temperature=0.1,
        max_tokens=80,
    )
    return result.visible_content.strip()


# ── report ────────────────────────────────────────────────────────────


@dataclass
class BenchReport:
    session_id: str
    scripted_turn_count: int
    state_truths: dict[str, Any]
    narrator_answers: dict[str, str]
    narrator_correct: dict[str, bool]
    narrator_recall_accuracy: float
    mutation_applied_count: int
    mutation_rejected_count: int
    mutation_rejection_rate: float
    total_cost_usd: float


def _coerce_for_json(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


async def run_bench(
    orchestrator: Orchestrator,
    keeper: WorldStateKeeper,
    *,
    scripted: list[str] | None = None,
    questions: list[FactQuestion] | None = None,
) -> BenchReport:
    scripted = scripted if scripted is not None else SCRIPTED_30
    questions = questions if questions is not None else FACT_QUESTIONS

    cost_at_start = orchestrator.client.total_cost_usd
    applied = 0
    rejected = 0

    for action_text in scripted:
        try:
            r = await orchestrator.take_turn(action_text)
            applied += len(r.applied_mutations)
            rejected += len(r.rejected_mutations)
        except Exception as e:
            # One bad turn shouldn't kill the whole bench.
            print(f"[bench] turn '{action_text}' failed: {e}")

    state_truths: dict[str, Any] = {}
    narrator_answers: dict[str, str] = {}
    correct: dict[str, bool] = {}
    for q in questions:
        truth = q.truth_fn(keeper.state)
        state_truths[q.id] = _coerce_for_json(truth)
        ans = await _ask_chronicler(
            orchestrator.client,
            orchestrator.narrator.model,
            orchestrator.narrator.prose_history,
            q.question,
        )
        narrator_answers[q.id] = ans
        correct[q.id] = bool(q.grade_fn(truth, ans.lower()))

    accuracy = (sum(correct.values()) / len(correct)) if correct else 0.0
    total_mut = applied + rejected

    return BenchReport(
        session_id=keeper.state.session_id,
        scripted_turn_count=len(scripted),
        state_truths=state_truths,
        narrator_answers=narrator_answers,
        narrator_correct=correct,
        narrator_recall_accuracy=accuracy,
        mutation_applied_count=applied,
        mutation_rejected_count=rejected,
        mutation_rejection_rate=(rejected / total_mut) if total_mut else 0.0,
        total_cost_usd=orchestrator.client.total_cost_usd - cost_at_start,
    )


def render_bench_report(r: BenchReport) -> str:
    n_correct = sum(r.narrator_correct.values())
    n_total = len(r.narrator_correct)
    lines = [
        f"session_id              : {r.session_id}",
        f"scripted_turns          : {r.scripted_turn_count}",
        f"narrator_recall         : {r.narrator_recall_accuracy:.0%} ({n_correct}/{n_total})",
        f"mutation_rejection_rate : {r.mutation_rejection_rate:.1%} ({r.mutation_rejected_count}/{r.mutation_applied_count + r.mutation_rejected_count})",
        f"total_cost_usd          : ${r.total_cost_usd:.4f}",
        "",
        "fact-by-fact:",
    ]
    for qid in r.state_truths:
        ok = "✓" if r.narrator_correct.get(qid) else "✗"
        truth = r.state_truths[qid]
        ans = r.narrator_answers.get(qid, "")
        lines.append(f"  {ok} {qid:24s} truth={truth!r:25s} narrator={ans!r}")
    return "\n".join(lines)


__all__ = [
    "BenchReport",
    "FactQuestion",
    "FACT_QUESTIONS",
    "SCRIPTED_30",
    "run_bench",
    "render_bench_report",
]
