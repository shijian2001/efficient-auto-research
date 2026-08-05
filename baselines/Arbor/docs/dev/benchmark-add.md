# Design note — `arbor benchmark add` (the collection feature)

> Status: design, not yet implemented. Captures the agreed architecture so the build
> doesn't start from a blank page. Internal dev doc (not in the published nav).

## Goal

`arbor benchmark add "<spec>"` — point Arbor at a benchmark you name (a paper, a GitHub
repo, a HuggingFace dataset id, or a plain description), and have it **acquire** the
materials, **bring up a runnable baseline + eval**, **draft** the docs, and produce a
*draft* zoo Task Pack that passes `arbor benchmark verify` — then hand it to a human to
accept. This is roadmap §2.1's "半自动转换 / intake agent": **drafting is automated,
acceptance is not, and it is never auto-accepted.**

Hard invariant (§2.1): the agent that brings up a baseline here is **separate** from the
loop that later optimizes it — collection stops *at* the baseline, never optimizes, so the
evaluation can't be self-certifying.

## Isolation — a separate top-level agent, not part of the research loop

The collector is its **own top-level agent/pipeline**, parallel to `arbor run`, built on the
shared `Agent` primitive (`src/core/agent.py:190`) the same way intake and the search-agent
are. It is **not** a coordinator "subagent" (that term means an Executor the Coordinator
spawns inside a research run) and it does **not** wire into the Coordinator/Executor loop.

This separation is a **correctness requirement, not just tidiness**: per §2.1 the
baseline-implementing agent must be distinct from the optimizing loop, or the eval is
self-certifying.

| Layer | Isolation guarantee |
| --- | --- |
| Code | lives in `src/zoo/`; `src/coordinator/` and `src/executor/` are untouched |
| Runtime | its own CLI (`arbor benchmark add`), its own process; never shares a research run's idea tree / session |
| Filesystem | works in the global cache + a scratch worktree; only touches `arbor-zoo/` on human accept |
| Dependency | it **imports** shared building blocks (Agent, providers, search tools, worktree helpers, `verify_pack`) — but reuse ≠ coupling; the research loop neither calls it nor is called by it |

Any helper agents the collector spawns (e.g. the Stage-2 baseline-bringup agent in a
worktree) are **its own** children, sealed inside the collection pipeline — also separate
from the Coordinator. Reuse the primitives; never hook into the main loop. (This is exactly
why `src/zoo/verify.py` was built dependency-light, with no import of `cli`/`coordinator`.)

## Positioning — runtime-sibling, product-subordinate

"Separate process" and "co-equal feature" are two different axes; this feature wants the
first, not the second. The core of Arbor is the research loop (`arbor run`). Collection is a
**supporting tool** on the external-resources line (方向二) — it *stocks the zoo shelf*; the
zoo is a resource that *feeds* `arbor run`. **It produces an input to Arbor; it is not
another Arbor.** Guardrails that keep the hierarchy clear (so the build doesn't drift):

- **CLI**: it stays `arbor benchmark add` — a tool subcommand under the `benchmark` noun,
  sibling to `verify`/`list`. Never a top-level `arbor add` or a headline command. `arbor
  run` remains *the* command.
- **Code**: it lives in `src/zoo/` (a resource subsystem), not a new top-level subsystem
  beside `coordinator/`/`executor/`.
- **Docs**: it belongs under the Benchmark Zoo guide, not in the README/nav headline.
- **Scope**: keep it lean and tool-shaped. A ballooning collector (its own GUI, its own
  large surface) is exactly what would blur the project's main/supporting structure — a
  second reason (beyond effort) to defer the GUI and keep v1 narrow.

Runtime-independent, dependency-reusing, product-subordinate — all three hold at once.

## Locked decisions

- **v1 covers both acquisition modalities**: a GitHub repo that already ships an
  eval/baseline, *and* a HuggingFace dataset with an API-judged metric. Kept cheap by a
  pluggable `Acquirer` interface (below) — "both" is +1 acquirer, not a second pipeline.
- **Downloads live in a global cache**: `~/.arbor/cache/benchmarks/<name>/` — reused across
  projects, git-ignored, never committed. License decides whether data is copied into the
  pack's `data/` or left in cache behind a `data/download.sh`.
- **Autonomy**: after the human confirms the target (Stage 0), run autonomously all the way
  to `verify` green, then hand the draft pack to the human for one acceptance gate.

## Why this is NOT "building a second Claude Code"

The agent runtime, search, isolated execution, eval, and the verifier already exist. The
collection feature is a *vertical workflow* on top of them. Net-new is essentially a
**download/data layer** + a **staged pipeline**. Reuse map:

| Need | Reuse | Entry point |
| --- | --- | --- |
| Spawn an agent (prompt + tools → run to completion) | `Agent` + `agent.run()` | `src/core/agent.py:190` |
| Interactive planning agent (read-only tools + a "done" signal tool) | `run_intake()` pattern | `src/cli/intake/repl.py:59`, `launch_tool.py:55` |
| Search papers/repos, read pages + PDFs | web backends + `ResearchSearch` | `src/core/tools/web/`, `src/coordinator/tools/research_ctx.py:183` |
| Isolated workspace, run shell, install deps | worktree + Bash/RunTraining | `src/mcp/session_ops.py:434`, `src/core/tools/bash.py:127`, `run_training.py:57` |
| Run eval, parse a score | `eval_run` / `parse_score` | `src/mcp/session_ops.py:367`, `:169` |
| **Decide the pack is good** | `verify_pack` (the Stage-2/4 oracle) | `src/zoo/verify.py` |
| Provider/model/key inheritance | `create_provider` / `resolve_config` | `src/core/__init__.py`, `config_resolve.py` |

Net-new (the real work):
1. **Download/data layer** — `git clone`, file download (resume), HF dataset fetch; a global
   cache + a small manifest (source, checksum, license). *Engineering-hard, bounded.*
2. **`Acquirer` interface** — see below; isolates "both modalities".
3. **`CreatePackTool` / pack writer** — scaffold `_template` → fill front-matter → place files.
4. **The collector pipeline** — `src/zoo/collect.py`, staged, resumable by cache.
5. **Stage system prompts** — resolve / baseline-bringup / doc-draft.
6. **CLI** — `arbor benchmark add`.
7. *(Later, not v1)* a review GUI — CLI diff + web UI suffices for v1.

## Origination — where a zoo task comes from

A zoo pack is **not "a dataset"; it is a locked optimization problem** =
`data + frozen substrate + edit surface + metric + reference baseline`. That reframing
drives how a task is sourced.

**Two modes** (literature-grounding is the default, not a universal mandate):

- **Literature-grounded** *(default for any benchmark that lives in a research literature)* —
  the task is constructed *from a body of representative papers*. You cannot know the
  community's de-facto eval harness, its general baseline, or the angles people climb from
  without reading the work. The survey *is* the provenance.
- **Constructed** *(escape hatch — synthetic / algorithmic / Kaggle / internal)* — the task is
  defined by its own generator + eval, with no paper leaderboard behind it (e.g.
  `algotune_knn`). Only light grounding (does a similar task exist? is the baseline sane?).

**Three entry points** (increasingly bundled — pick by what you start from):

- **Benchmark-first** — "collect GSM8K": resolve *this* benchmark's canonical source + baseline
  + angles.
- **Direction-first** — "survey search agents": survey a research **direction's** representative
  papers and **harvest from that one survey both (a) the benchmarks the field actually competes
  on, and (b) the baseline implementations those works share/open-source.** One direction → a
  fan-out of `(benchmark, baseline)` candidates → the human picks which to onboard. The
  directions in the landscape survey (agents, reasoning, RAG, efficiency, …) are these inputs.
- **Work-first** *(the most bundled — primary engine for the frontier layer below)* — anchor on a
  single representative **hot work** (a paper + its repo). One work is a *pre-packaged bundle*:
  its **code = the baseline**, the **benchmark it reports on = the eval**, its **reported number
  = the anchor to beat**, and its **mechanism = the angle** (Agentless → scaffold; a fine-tuning
  paper → training; GEPA → prompt). Work-first does **not** drop the benchmark — it lets the work
  *select and bundle* the benchmark + baseline + angle, so it is information-strictly-richer than
  benchmark-first. The task becomes "beat this work's result by editing its mechanism."

**Why harvesting matters operationally:** direction-first and work-first hand you a *runnable
baseline* from a representative repo, so the survey **harvests** an implementation instead of
inventing one — collapsing the hardest stage (Stage 2 bring-up) from *"invent a baseline + eval
for an arbitrary repo"* down to *"make this harvested baseline run + wrap a clean dev/test eval."*
Work-first goes furthest: the work *is* the baseline.

**What the survey must pin (the per-benchmark task spec), all human-gated at Stage 0:**

1. **Canonical source** — the repo/commit/harness people *actually run*, which is frequently a
   representative work's curated data + eval, **not** the upstream official dataset. Pin to a
   commit; record *why this is the de-facto standard* in PROVENANCE.
2. **Reference baseline** — surveyed across several papers, distinguishing four reference points:
   `floor` (naive), **`general baseline`** (the community-standard method → the front-matter
   `baseline.score` anchor), `SOTA` (the target ceiling), and a paper's own implementation.
   Prefer harvesting the general baseline's *implementation* from a representative repo.
3. **Angle decomposition + the freeze axis** — the *same dataset is climbed from different
   angles* (prompt vs fine-tune vs tool-scaffold). **The angle *is* the pack:** its `edit:`
   surface + what it freezes + its metric. One dataset → several angle-locked packs. The
   coverage of each pack is set by **what it freezes**, a deliberate choice (recorded in the
   optional `frozen:` field):
   - **Freeze the model** (`edit: [scaffold]`) → covers only scaffold/inference works; clean
     attribution, cheap, narrow.
   - **Freeze only a budget** (compute/cost/wall-clock; `edit: [scaffold, training, data]`) →
     covers *all* mechanisms (training, RL, scaffold) on equal footing; comparability comes from
     the fixed budget, not a fixed substrate. This is MLE-bench's design and stays controlled
     (budget + held-out), not a trophy board — but needs a single budget currency (wall-clock on
     fixed hardware) and the compute to actually run those mechanisms.
   Arbor's ideation is mechanism-agnostic: give it a broad `edit:` + a budget and its Idea Tree
   branches across train/scaffold/ensemble on its own, and `merge` combines them — coverage that
   single-mechanism papers can't give. The practical ceiling is compute: on 4×A100 the training
   mechanism is only reachable for small-model benchmarks; SWE-bench realistically freezes the
   model. **A pack must declare its freeze** so "improvement" is attributable and two runs are
   comparable.

### Two layers — stable core + work-anchored frontier

Work-first and benchmark/synthetic-first are not either/or; they serve the zoo's two purposes:

- **Stable core** *(benchmark / synthetic-anchored, e.g. `algotune_knn`)* — Arbor's own
  **regression harness**: stable, cheap, deterministic, doesn't drift with the hype cycle.
- **Frontier shelf** *(work-anchored)* — a rotating set of "beat this hot method" tasks built
  **work-first**. Current and exciting — this is the "刷 hot directions" use case. Direction-first
  surveys naturally surface the works that populate this shelf.

The core keeps Arbor honestly regression-tested; the frontier tracks the field. Work-first is the
primary engine for the frontier; the constructed mode anchors the core.

### Guardrail — work-first is one line away from AutoSOTA

Work-first ("anchor on a paper, beat its number") sits *closest* to the AutoSOTA niche the
roadmap explicitly says Arbor is **not**. The line that keeps it legitimate:

| AutoSOTA (do NOT become) | work-first zoo (OK) |
| --- | --- |
| Maintain a per-paper **SOTA scoreboard** | Use a representative work as the **starting artifact + objective definition** for a reusable, verifiable task |
| Deliverable = *our* trophy numbers | Deliverable = a **re-runnable task** (frozen objective + held-out) others can take |

So: papers/works are used to *position and fairly-baseline a reusable optimization task* — never
to catalogue how many papers we beat. Keep that line and work-first stays inside the roadmap.
Practical filter (which also dodges reproduction hell): only onboard works whose **repo runs**,
**fit 4×A100/API**, and **report a clean benchmark + metric**.

**Format impact:** minimal. The angle is the existing `edit:`/protected mechanism; canonical
source, baseline rationale, and the floor/SOTA reference points live in PROVENANCE prose. At
most add two *optional* front-matter fields — `frozen:` (pinned base model / budget) and
`references:` (floor/sota; the anchor stays `baseline.score`) — rather than re-fattening the
format we deliberately slimmed.

## Pipeline


```
spec ─▶ Stage 0 Survey & Disambiguate (literature survey + search)
          input = a research DIRECTION ("search agents") or a single benchmark ("GSM8K")
          → read N representative papers; harvest (benchmark, baseline-impl) candidates
          → per benchmark, a task spec: canonical source+commit, baselines
            (floor/general/SOTA), angle decomposition, frozen substrate, feasibility
          → HUMAN gate: confirm source + baseline + angle(s)  (a direction → several packs)
        Stage 1 Acquire (deterministic + download layer)        ── Acquirer.acquire()
          → clone/download into ~/.arbor/cache/benchmarks/<name>/, record provenance
          → license: copy into pack/data OR write data/download.sh
        Stage 2 Bring up baseline + eval (baseline agent in a worktree)
          → make the HARVESTED baseline run (not invent one); wrap a clean dev/test eval
            that prints `score:`; fill README front-matter contract
          → ORACLE: loop until `verify_pack` is green (or report blockers)
        Stage 3 Draft docs (agent, reuse Stage-0 survey material)
          → README body (4 sections) + PROVENANCE (7 sections incl. baseline impl +
            DRAFT contamination assessment)
        Stage 4 Verify + HUMAN accept
          → final `arbor benchmark verify`; show draft pack + report; human edits/accepts
          → on accept: move into arbor-zoo/<name>/   (never auto-accept)
```

Orchestration is a plain deterministic Python sequence (not a heavyweight multi-agent
framework): each stage spawns an `Agent` with a stage-specific prompt + toolset; the
verifier is the success oracle for Stages 2 and 4; resume keys off the global cache. A
direction-first Stage 0 fans out — one survey can spawn several packs (Stages 1–4 per
benchmark). Stage 0 is the heaviest and most human-gated stage, and it reuses the
already-shipped grounding search (§1.1 / `ResearchSearch`) rather than new machinery.

## The `Acquirer` interface (keeps "both modalities" cheap)

```python
@dataclass
class Sources:        # produced by Stage 0 (the survey)
    kind: str         # "git" | "hf"
    locator: str      # canonical repo URL+commit | HF dataset id
    license: str | None
    baseline_ref: str # harvested baseline impl: repo path / method to reproduce (de-risks Stage 2)
    angle: str        # the locked angle: what's editable vs frozen (base model, budget)
    notes: str        # metric, floor/general/SOTA reference points, feasibility

@dataclass
class Acquired:       # produced by Stage 1
    cache_dir: Path   # ~/.arbor/cache/benchmarks/<name>/
    manifest: dict    # {source, checksum, license, files}

class Acquirer(Protocol):
    def matches(self, spec: str) -> bool: ...
    def resolve(self, spec: str, search) -> Sources: ...      # may use ResearchSearch
    def acquire(self, sources: Sources, cache_dir: Path) -> Acquired: ...
    def bringup_recipe(self) -> str: ...   # extra system-prompt guidance for Stage 2
```

v1 ships two: `GitRepoAcquirer` (clone; bring-up = adapt the repo's existing eval) and
`HFDatasetAcquirer` (fetch dataset; bring-up = construct an API-judged scorer + dev/test
split). Everything after Stage 1 is shared.

## Honest difficulty ranking

1. **Stage 2 generality** — "make a research repo run and emit a score on a clean held-out
   split". Research-hard, but it is *Arbor's home turf* (the executor already does this) and
   the verifier bounds "done". **Direction-first Stage 0 de-risks this a lot**: when the survey
   harvests an existing baseline impl from a representative repo, Stage 2 becomes "make *this*
   run + wrap an eval" rather than "invent a baseline". Difficulty still swings by benchmark: a
   repo with a clean eval is easy; one needing an invented scorer + split is hard.
2. **Stage 0 survey quality** — picking the *de-facto* canonical source, the *general* baseline
   (not an arbitrary one), and the right angle decomposition. This is judgement-heavy and is
   exactly why Stage 0 is human-gated; a wrong call here makes a clean-looking but unfair pack.
3. **Stage 1 acquisition for messy real sources** — auth, large files, HF/Kaggle quirks,
   license gating. Engineering-hard, bounded.
4. **Honest contamination / held-out judgement** — needs a human. Automate the *draft*, never
   the *acceptance*.

## Phased build order (sequences "both" safely)

1. **Shared spine + git modality**: global cache + manifest, `CreatePackTool`, the
   `collect.py` pipeline, `GitRepoAcquirer`, Stage prompts, `arbor benchmark add` CLI →
   prove end-to-end on ONE real repo-with-eval (the algotune_knn analogue for collection).
2. **HF + API modality**: add `HFDatasetAcquirer` + the API-judged bring-up recipe → prove on
   one reasoning/agent benchmark.
3. **Hardening**: resume, cache dedup/locking, license edge cases, better blocker reporting.
4. *(Later)* review GUI / web-UI acceptance surface.

Each phase ends with one real, human-accepted pack — coverage proven by working examples,
not by breadth of untested code paths.
