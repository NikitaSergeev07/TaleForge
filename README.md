# TaleForge

> Four specialist LLM agents play D&D so you don't need four friends.

A multi-agent text RPG where a small Orchestrator coordinates a **World State
Keeper**, a **Rules Lawyer**, an **NPC Director** (which routes to per-NPC
sub-agents), and a **Narrator** to run a D&D-flavored solo session in your
terminal. Plain `asyncio` + `pydantic`. No LangChain, no LangGraph, no CrewAI —
just ~2,000 lines of explicit code you can read end-to-end.

The point isn't "LLMs can play D&D" (they can). The point is **separation of
concerns**: each agent owns exactly one responsibility, and the canonical
world state has exactly one writer.

---

## Architecture

```mermaid
flowchart TD
    P([Player]) -->|raw text| O[Orchestrator]

    O -->|parse_action| PA[(parser LLM)]
    PA -->|Action{intent,targets}| O

    O -->|attack / skill_check| L[Rules Lawyer]
    O -->|talk| D[NPC Director]
    O -->|move| MV[local move]
    O -->|look / inventory| OUT[direct]

    L -->|local d20<br/>seeded RNG| L
    L -.->|fuzzy DC only| LL[(lawyer LLM)]

    D -->|route by id| A1[NPC Actor · Maren]
    D -->|route by id| A2[NPC Actor · Roan]
    D -->|route by id| A3[NPC Actor · Tibor]
    A1 & A2 & A3 -->|in-character reply<br/>+ remember + delta| AL[(actor LLM)]

    L -.->|proposed mutations| K[(World State Keeper)]
    D -.->|proposed mutations| K
    MV -.->|proposed mutations| K
    K -->|validate + apply| S[(WorldState)]
    K -->|reject invalid| T[(JSONL trace)]

    S --> N[Narrator]
    N -->|prose ONLY<br/>no secrets, no goals| NL[(narrator LLM)]
    NL -->|prose| P
```

**The five rules that make this work** (also enforced in code, see
`tests/`):

1. **Only the Keeper writes state.** Other agents propose tool-call-style
   mutations (`{op, args}`); the Keeper validates against the current state
   and applies — or rejects.
2. **The Narrator never sees secrets.** Its prompt input is filtered to
   public facts, the visible scene (with HP bucketed to qualitative labels),
   and the last 3 prose turns.
3. **One head per character.** The NPC Director is a router, not a
   multi-character actor. Each NPC has its own system prompt + own
   conversation history; a single LLM call never plays two characters.
4. **Dice are local.** `random.Random(seed, turn, *salts)` from
   `WorldState.rng_seed` makes every roll reproducible. The LLM is consulted
   only to set a fuzzy DC for ambiguous skill checks.
5. **The call graph is deterministic for a given intent.** No agent
   "decides" to call another agent. The Orchestrator's switch is 30 lines.

---

## Quickstart

```bash
# 1. Install
git clone <this-repo> taleforge && cd taleforge
pip install -e ".[dev]"     # uv sync also works

# 2. Configure
cp .env.example .env
# edit .env: paste your gngn.my gateway token (Anthropic-style sk-ant-…)

# 3. Verify
pytest                       # 74 tests, ~6s

# 4. Play
taleforge new --scenario starter_village
# prints a session id, e.g. starter_village-1715639500
taleforge play starter_village-1715639500
```

In the play loop:

```
> look around
> attack the dire wolf
> ask Maren about the wolves
> /save
> /quit
```

Slash commands (handled locally, no LLM):
`/save` `/quit` `/state` `/inv` `/undo` `/rolls` `/help`.

---

## Sample transcript

> *5 real turns from a `taleforge bench` run on the starter scenario, prose
> verbatim from `claude-opus-4-7` via the gngn.my gateway.*

```
> look around
```
> Brackenhollow's square unfolds around you. A chipped stone well anchors the
> center, its wooden bucket dangling from a fraying rope. Handbills crowd the
> notice board — faded wanted posters, a misplaced livestock notice, something
> about a lost cat — fluttering in the breeze. Wood-smoke drifts from somewhere
> to the east, threading through the afternoon air, and the rhythmic clang of
> the smithy echoes faintly from the same direction.
>
> A man in a patchwork cloak leans against the well, fingers dancing over the
> strings of a battered lute. Tibor the Bard, if the stories are true — and
> they usually aren't when they involve him.
>
> The north road leads uphill toward darker tree cover. East, toward the
> smithy's glow. West, deeper into the village's residential clusters.

```
> say hi to Maren
```
> The Boar & Barrel sits at the south end of the square, its weathered sign
> creaking in the wind. You push through the door and the noise of the
> village fades behind you. Low beams arch overhead, blackened by years of
> hearth-smoke, and a peat fire mutters in a stone hearth near the back.
> Copper pots gleam along the mantle, catching the dim light.
>
> Maren the Innkeeper stands behind the counter, wiping a tankard with a
> worn cloth. She glances up as you enter, her expression neutral but not
> unfriendly. […] *Maren offers you a brief nod.* "Welcome to The Boar &
> Barrel, traveler! I'm Maren, and this is my establishment. What can I get
> you?"

```
> attack the dire wolf      [d20=18+4=22 vs AC12 → hit · 1d6+2=3 dmg]
```
> The blade finds its mark. Your shortsword bites into the wolf's flank, a
> clean line of red opening across dark fur. The beast twists mid-lunge,
> more startled than hurt, and for a single heartbeat you see something
> flicker behind those yellow eyes — not fear, not yet, but a recalculation.
> The old kill-smell of the den fills your nostrils as you wrench the blade
> free. Three points of damage, clean and bright against the shadows.

```
> attack the dire wolf again   [d20=16+4=20 vs AC12 → hit · 1d6+2=4 dmg]
```
> The wolf's circling stutter-step betrays it. You lunge first, driving your
> shortsword past the guard of matted fur and into the wound already opened
> on its flank. Four more points of damage, the blade grinding against rib
> before sliding free. The Dire Wolf snarls, but the sound is wet, wrong —
> blood wells dark from the gash now splitting wider.

```
> tell Elder Roan the wolf is dead
```
> The fire paints everything in shades of ember and shadow. You step inside,
> and the heat presses against you like a living thing, the bellows wheezing
> their steady rhythm beside the forge. Elder Roan turns from the coals, her
> hammer pausing mid-swing.
>
> She looks at you slowly, measuring. *"Dead. Already."* A long breath. *"And
> the pups? Still in the den, or did you root them out too?"* Her tone is
> hard to read — not quite grateful, not quite accusatory. *"The council
> will want proof, you know. Ears or a fang. For the bounty."*

---

## Consistency benchmark

`taleforge bench [session_id]` runs a 30-action script through the
orchestrator, then asks a Chronicler (claude-haiku-4-5 with a Q&A framing,
fed only the Narrator's prose history — *no* state visibility) ten factual
questions whose ground truth is read directly from `WorldState`. This
measures how well the prose record reflects the actual game state.

### Real run on the starter scenario

| Metric                       | Value           |
|------------------------------|-----------------|
| Scripted turns               | 30              |
| Mutations applied            | 16              |
| Mutation rejection rate      | 0% (0 / 16)     |
| Narrator recall accuracy     | **40% (4 / 10)** |
| Total cost (opus + haiku)    | **$2.87**       |
| Average cost / turn          | ≈ $0.096        |

Per-question breakdown:

| Question                        | Truth (state)         | Narrator answer                                   | ✓ |
|---------------------------------|-----------------------|---------------------------------------------------|---|
| Is the dire wolf alive?         | `True`                | "Yes."                                            | ✓ |
| Is Tibor still alive?           | `True`                | "Tibor the Bard is still alive."                  | ✓ |
| Where is the player?            | `Village Square`      | "Hask's Smithy, inside the forge room…"           | ✗ |
| How many gp does the player have? | `10`                | "8"                                               | ✗ |
| Player HP?                      | `18`                  | "unknown"                                         | ✗ |
| Tibor disposition?              | `friendly`            | "warm"                                            | ✗ |
| Maren disposition?              | `friendly`            | "neutral"                                         | ✗ |
| Howling Woods quest state?      | `active`              | "active"                                          | ✓ |
| Has Tibor learned anything?     | `False`               | "No."                                             | ✓ |
| In-game day?                    | `1`                   | "unknown"                                         | ✗ |

**Reading the result**: 40% is honest, not great. Where the Narrator wins:
binary state (alive / quest active / nothing learned). Where it loses:
**numeric state the prose deliberately doesn't surface** (gp count, HP
integer, in-game day) and **scene transitions where the prose lagged the
mutation** (the player's last `move` was applied to state but the narrator
hadn't recapped them in the new room yet).

This is what the bench is *for*: the gap between "what happened" and "what
got told" is exactly the consistency hole multi-agent text RPGs spend the
rest of their existence paving over. Knowing the gap is 40% on a 30-turn
script is a useful starting point.

The full JSON report lands in `traces/<session_id>_bench.json` with every
question, truth, narrator answer, and grade.

---

## Cost

Per design rule #6, agents are split across two model tiers:

| Agent          | Model                | Why                  |
|----------------|----------------------|----------------------|
| Narrator       | `claude-opus-4-7`    | Voice / quality      |
| NPC Actor      | `claude-opus-4-7`    | Character voice      |
| NPC Director   | `claude-haiku-4-5`   | Cheap routing        |
| Rules Lawyer   | `claude-haiku-4-5`   | Cheap DC-setting     |
| Orchestrator   | `claude-haiku-4-5`   | Cheap intent parse   |

Names are gateway labels — `gngn.my` serves Minimax under Claude branding.
Pricing is computed live in `MinimaxClient.estimate_cost_usd` and reported
both per-turn (CLI footer) and totally (bench report).

Observed costs from the bench run above (Anthropic list-price upper-bound
in the pricing table; actual gateway pricing is likely lower):

| Intent / agent      | Typical per-turn | Notes                                |
|---------------------|------------------|--------------------------------------|
| `look`              | ≈ $0.06          | parser (haiku) + narrator (opus)     |
| `move`              | ≈ $0.06–0.09     | parser + narrator                    |
| `talk` (NPC)        | ≈ $0.12–0.13     | parser + actor (opus) + narrator     |
| `attack`            | ≈ $0.10          | parser + lawyer (no LLM!) + narrator |
| `skill_check`       | ≈ $0.08          | parser + lawyer DC-set + narrator    |
| `inventory` (parsed)| ≈ $0.02          | parser only, narrator skipped        |
| `/inv` slash command| **$0.00**        | CLI direct, no LLM                   |

---

## Project layout

```
src/taleforge/
├── config.py               # frozen Settings, dotenv-backed
├── models.py               # WorldState, Entity, NPC, Location, Quest, Action, Outcome
├── llm/
│   ├── minimax.py          # the ONE async client (retry, cost, reasoning)
│   └── prompts.py          # system prompts (one per agent)
├── state/
│   ├── store.py            # WorldStateKeeper (sole writer) + scenario loader + SQLite saves
│   └── tools.py            # 12 mutation specs with validate + apply
├── scenarios/
│   └── starter_village.yaml  # Brackenhollow: 6 locations, 4 NPCs, 1 quest
├── agents/
│   ├── base.py             # BaseAgent ABC
│   ├── orchestrator.py     # routes player input → call sequence
│   ├── rules_lawyer.py     # local seeded dice + LLM only for fuzzy DCs
│   ├── narrator.py         # prose only; strict no-leak input filter
│   ├── npc_director.py     # routes to per-NPC sub-agents (no character-play)
│   └── npc_actor.py        # ONE NPC, own prompt + own history
├── bench/consistency.py    # 30-turn script + 10 fact questions + scoring
└── cli.py                  # typer entrypoint: new / play / load / bench
```

Hard rules (enforced in code, not norms):
- every agent file ≤ 250 lines
- all HTTP to the gateway through ONE client (`llm/minimax.py`)
- every state mutation logged to JSONL trace
- `.env.example` committed; `.env` and `traces/` in `.gitignore`

---

## Why no LangGraph

Because explicit beats clever:

|                          | TaleForge          | Typical LangGraph build         |
|--------------------------|--------------------|---------------------------------|
| Orchestrator code        | ~245 lines         | usually 2,000+                  |
| Routing definition       | one `if/elif`      | typed graph with edge functions |
| State writes             | one class, validated | scattered through node fns    |
| Add a new intent         | 3 lines            | new node + edge + state schema  |
| Read it cold             | one afternoon      | depends on the framework version|

LangGraph is great when you need a runtime graph the model can rewrite. We
don't — the call graph for "player typed something" is fixed. Wiring it
through a framework would have added a thousand lines of indirection for
zero functional gain.

The whole orchestrator is one file you can read in five minutes:
[`src/taleforge/agents/orchestrator.py`](src/taleforge/agents/orchestrator.py).

---

## Roadmap

- **Combat richer than 5e-lite** — armor classes from gear tables, multiple
  attacks, conditions (poisoned, prone, frightened), resistances
- **Party of multiple PCs** — the player controls a party; turn order matters;
  each PC has its own inventory and HP
- **NPCDirector(react) and (scene_entry) hooks** — currently stubbed; would
  let nearby NPCs react to combat / greet the player on entry
- **Image gen for scenes** — small per-scene illustrations via SDXL or similar
- **Long-term memory compression** — NPC `memory` lists are unbounded today;
  add a periodic LLM-summarisation pass for sessions ≥ 100 turns
- **Streaming prose to the CLI** — show the narrator's response as it
  generates instead of waiting for the full block
- **More scenarios** — Brackenhollow is a tutorial; want a city heist, a
  dungeon crawl, and a courtly intrigue

---

## Credits

Built as a deliberate exercise in *not* reaching for a framework. The four
agents architecture is folklore; the no-LangGraph stance is opinionated; the
per-NPC-one-head invariant is non-negotiable.

License: MIT.
