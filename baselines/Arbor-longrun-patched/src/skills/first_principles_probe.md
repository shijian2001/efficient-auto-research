---
name: first_principles_probe
description: First-principles diagnostic checklist before ideation.
when_to_apply: When deeper diagnostic framing is needed before ideation. Do not reconstruct from memory.
---

# SKILL: First-Principles Probe

**When to apply:** Stage A of every IDEATE round.
**Output:** A "Probe Block" pasted into your reasoning trace. It is not passed to any tool, but downstream candidates should clearly trace back to it.

**Why it exists:** The default failure mode of LLM-driven research is to propose plausible-sounding engineering trickery without ever naming the bottleneck. The probe forces an explicit causal story BEFORE any candidate exists, so candidates can be checked against a stated bottleneck rather than retrofitted to one.

**The four questions** (answer each in 2–4 sentences, each grounded in concrete evidence — log lines, failure cases, code references):

1. **First Principles** — What is the core bottleneck, reasoned from the mathematical / algorithmic essence of the task? Distinguish failure CLASSES, not failure instances. Pick the right axes for the task; some useful framings: wrong retrieval / wrong reasoning over correct evidence / wrong stopping condition / wrong representation / wrong objective / wrong action space / wrong credit assignment. Cite ≥2 concrete failure cases as evidence. If you cannot point to specific failure cases, you have not OBSERVed enough — go back to Step 1.

2. **Hidden Assumption** — What load-bearing assumption does the current trunk silently rely on? Examples by domain: "queries should be reformulations of the original question"; "retrieval is one-shot"; "the answer lives in a single passage"; "the loss is well-calibrated"; "tools are stateless"; "the test distribution matches train"; "executors agree on what success means". Name the assumption, then describe what becomes possible if it is dropped.

3. **Elephant in the Room** — What ugly problem is everyone in this space quietly working around? Real-world friction the benchmark may understate, or a failure mode the trunk handles by ignoring it. The best ideas often attack the elephant directly.

4. **Hamming's Question** — If the bottleneck named in (1) were magically solved, would the benchmark / the field meaningfully change? If "not really", the bottleneck is wrong — re-do (1).

**Output format (paste into reasoning trace):**

```
PROBE BLOCK
1. First principles : <bottleneck CLASS> — evidence: <case ids / log refs>
2. Hidden assumption: <assumption> — if dropped: <what opens up>
3. Elephant         : <ugly problem the trunk currently ignores>
4. Hamming          : <yes/no, plus one sentence justification>
```

If any line is vague ("the bottleneck is performance", "the assumption is that the system works"), the probe has failed — redo it before proceeding to Stage B.
