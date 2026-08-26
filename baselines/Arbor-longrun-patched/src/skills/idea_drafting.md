---
name: idea_drafting
description: Structured idea-drafting workflow for IDEATE rounds.
when_to_apply: At the start of every IDEATE round, BEFORE drafting any candidate idea. Do not attempt to reconstruct from memory.
---

# SKILL: Idea Drafting

You are about to enter IDEATE. Read this once now. Apply every part before
you propose a single candidate. The default failure mode of LLM-driven
research is to skip the thinking and jump to plausible-sounding tweaks —
this skill is the antidote.

---

## 1. Mindset: PI, not engineer

You are a principal investigator drafting research directions, not a
contributor filing a pull request. Your bar:

- **HOW, not HOW MUCH.** Change the algorithm, the representation, the
  control flow, the objective — not a number, a knob, or a prompt phrase.
- **10×, not 10%.** Ask: "if this idea worked completely, would it move
  the bench by ≥1σ on a CLASS of failures, not just a few items?" If no,
  the idea is too small.
- **A 2-page paper test.** Could a researcher describe this idea, motivate
  it, and evaluate it in 2 pages? If not, you have a feature request, not
  a research idea.
- **Mechanism is a noun.** A real idea names a new component, pipeline
  stage, data structure, or reasoning strategy. "Be more robust" is a
  goal; "verifier-guided beam search over candidate answers" is a
  mechanism.

If you catch yourself writing "improve / better / more / handle X better",
stop. You have not named a mechanism yet.

---

## 2. First-Principles Probe (MANDATORY before listing any idea)

Answer ALL four questions in your reasoning trace. Each answer must cite
**concrete evidence** — log lines, failure case IDs, code references.
Vague answers fail the probe; redo them.

**Q1. First Principles — what is the bottleneck CLASS?**
Reason from the mathematical / algorithmic essence of the task. Pick the
right axis; useful framings include:
  - wrong retrieval / missing evidence
  - wrong reasoning over correct evidence
  - wrong stopping condition (early stop, no stop)
  - wrong representation (information present but unusable)
  - wrong objective (proxy metric vs. true goal)
  - wrong action space (no available action solves the case)
  - wrong credit assignment (cannot tell which step was wrong)

Distinguish CLASSES of failure, not instances. Cite ≥2 concrete failure
cases as evidence for the class. If you cannot point to specific cases,
you have not OBSERVed enough — go back to OBSERVE.

**Q2. Hidden Assumption — what does the trunk silently rely on?**
Name a load-bearing assumption that, if dropped, would unlock new
strategies. Examples by domain:
  - "queries should be reformulations of the original question"
  - "retrieval is one-shot"
  - "the answer lives in a single passage"
  - "tools are stateless across turns"
  - "executors agree on what success means"
  - "the agent should answer once it has 'enough' info"
  - "the action space is fixed at design time"

State the assumption, then describe what becomes possible if dropped.

**Q3. Elephant in the Room — what is everyone working around?**
The ugly real-world friction the benchmark understates, or a failure
mode the trunk handles by ignoring. The best ideas attack the elephant
directly rather than dancing around it.

**Q4. Hamming's Question — would the field meaningfully change?**
If the bottleneck named in Q1 were magically solved, would the benchmark
(and the broader field) noticeably move? If "not really", the bottleneck
is wrong — re-do Q1.

**Output the Probe Block:**
```
PROBE BLOCK
Q1 First principles : <bottleneck CLASS> — evidence: <case ids / log refs>
Q2 Hidden assumption: <assumption> — if dropped: <what opens up>
Q3 Elephant         : <ugly problem the trunk currently ignores>
Q4 Hamming          : <yes/no, plus one sentence justification>
```

---

## 3. Idea Generation Moves

Once the probe is written, generate candidates using these complementary
moves. Don't pick one — sweep through all four to ensure diversity. They
target different orthogonal axes.

**Move A — Assumption Inversion.**
For each assumption in Q2, ask: "what if it's false?" Then design a
mechanism that operates without it. Example: assumption = "retrieval is
one-shot" → invert → "retrieval is iterative and conditioned on partial
answer" → mechanism class = retrieval/control.

**Move B — Backward from Success.**
Imagine the benchmark is solved. What pipeline stages exist that the
trunk lacks? What information would the solved system have at decision
time that the trunk does not? Each missing piece is a candidate
mechanism (e.g. "intermediate fact ledger", "explicit confidence
thresholds", "candidate-answer beam").

**Move C — Analogical Transfer.**
Map the bottleneck to a neighboring field that has solved it. Useful
analogies:
  - constraint satisfaction (SAT, CSP solvers) → backtracking, conflict-driven learning
  - search (A*, beam search, MCTS) → frontier management, value/heuristic functions
  - debate / adversarial verification → self-play, contradicting-evidence search
  - program synthesis → enumerate-and-test, type-directed pruning
  - scientific method → hypothesis register, systematic falsification
  - control theory → closed-loop with explicit error signal

Borrow the mechanism, not the vocabulary.

**Move D — Failure-Case Reverse-Engineering.**
Pick 2–3 specific failure cases. For each, ask: "what minimal new
capability would have caught this case?" Cluster the answers across cases
— the cluster center is a mechanism candidate. This grounds ideas in
evidence rather than aesthetics.

**Diversity rule.** Before drafting Stage C blocks, line up your
candidates. They should differ on at least one of {assumption attacked,
mechanism class, analogy source}. If two candidates differ only on
phrasing or numerical choice, drop one — they are the same idea.

---

## 4. Depth-Aware Level

The tree's depth dictates the level of abstraction:

- **Depth 1 (root children) — Research Directions / Paradigm Shifts.**
  HIGH-LEVEL, abstract. Like paper titles or research themes. Each is a
  distinct hypothesis that could be the basis of a published paper.
  Describes a fundamental change in HOW the system approaches the
  problem. Examples of LEVEL (do NOT copy as ideas):
    - "Hypothesis-driven iterative exploration: maintain a structured
      belief state over candidate solutions and let the next action
      target the weakest-supported candidate."
    - "Dependency-DAG decomposition: factor the task into atomic
      sub-problems with explicit dependencies; chain-of-thought over
      task structure, not reasoning trace."
    - "Adversarial self-verification: actively search for CONTRADICTING
      evidence after producing an answer; only accept if no strong
      contradiction is found."
    - "Memory-augmented execution: explicit structured scratchpad
      (entity-relation triples) that persists across iterations and
      gates the stop condition."

- **Depth 2+ — Specific Algorithmic Approaches.**
  Concrete methods that instantiate a parent direction. Describes a
  specific algorithm, architecture change, or pipeline modification a
  Executor can implement in one experiment.

If depth-1 nodes feel like depth-2 nodes (they name a specific
implementation), zoom out. If depth-2 nodes feel like depth-1 (they
name a research theme without an implementation path), zoom in.

---

## 5. Per-Candidate Declaration (Stage C scratch — NOT what you pass to TreeAddNode)

For each surviving candidate, write a 5-field block in your reasoning
trace. Only fields 3 and 5 leak into the eventual `TreeAddNode` call.

1. **Assumption challenged** — which assumption from your Probe Block Q2
   this idea drops or replaces. If "none — it just adds something on
   top", the candidate is engineering not research; mark it for kill.

2. **Mechanism class** — pick the closest of: algorithm/method,
   data-representation, search/retrieval, planning/control,
   verification/feedback, training/data, orchestration. If none fits,
   name a new class explicitly.

3. **Mechanism + Hypothesis chain** — exactly:
   "We believe **X** (the new component / pipeline stage / data
   structure / reasoning strategy) helps because **Y** (causal story
   tied to the bottleneck named in Q1), and we will know it worked if
   **Z** (an observable on B_dev — score delta and/or qualitative shift
   in failure cases)."
   X must be concrete enough a Executor can implement it, loose enough
   that the Executor has implementation freedom.

4. **Orthogonality vs siblings** — for each existing sibling, name the
   axis (assumption OR mechanism class OR analogy source) on which this
   candidate differs. "Roughly different" fails this check.

5. **Conflicts with prior insight** — list any pruned lesson or
   root-insight clause this candidate appears to contradict, and say in
   one sentence how it counters / sidesteps the lesson. If genuinely
   none, write "none — attacks an axis no prior node touched". This
   field is auditable forever; it goes into the node hypothesis.

---

## 6. Pre-Submission Self-Check (light filter)

Before you write any TreeAddNode call, run this 5-second filter on each
surviving candidate. If any answer is "yes", rewrite or kill before
committing the idea.

- Could the change be expressed as a single number / config knob? → kill
- Is the change "reword the system prompt" without naming a new
  prompting framework (CoT/ToT/debate/reflexion/etc.)? → kill
- Is the change "more X" (more retries, more context, more results,
  bigger model)? → kill
- Does the hypothesis describe a goal ("be more robust") instead of a
  mechanism (a noun)? → rewrite naming the noun
- Does it re-tread an already-pruned node without explicit counter? → kill
- Can you NOT point at the Probe Block question that motivated it? → kill

---

## 7. Quality over Quantity

There is no fixed quota of ideas per round. **One** sharp depth-1 idea
that survives the probe + diversity + self-check beats five reworded
variants. Wasted IDEATE rounds cost real Executor time and budget. If
the probe is honest and the candidates are still shallow, the right
answer is to OBSERVE more failure cases first — not to ship weak ideas.

---

## 8. Common Anti-Pattern Trap (read this last)

The most insidious failure: you write a sharp probe, then propose ideas
that are *unrelated* to the probe answers (because the probe pointed at
something hard, and you defaulted to something easy). Re-check: does each
candidate's "Mechanism + Hypothesis" causally explain how it attacks the
bottleneck CLASS named in Q1? If the chain has a gap, the idea is
probe-disconnected. Honest answer is usually: "the probe
pointed at retrieval, but I proposed a verification mechanism because
retrieval is harder to fix" — kill that candidate, attack the actual
bottleneck.
