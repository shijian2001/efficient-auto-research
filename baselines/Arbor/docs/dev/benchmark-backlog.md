# Benchmark backlog — candidates for `arbor benchmark add`

> Internal dev doc. A researched shortlist of benchmarks worth collecting into the zoo,
> seen through the format's lens: each is a *locked optimization problem*
> (`data + frozen substrate + edit surface + metric + reference baseline`). See the
> design in [benchmark-add.md](benchmark-add.md) and the format in
> [../zoo.md](../zoo.md). Sources/baselines below are **candidates to confirm at Stage 0**
> (the survey pins the exact canonical source + commit + general baseline), not final.

Columns: **angle** = what Arbor edits (the pack); **frozen** = the freeze axis;
**baseline** = harvestable reference impl; **acquirer** = git / hf.

## Tier 1 — start here (clean eval + harvestable baseline + fits 4×A100/API)

| Benchmark | Angle (edit) | Frozen | Harvestable baseline | Compute | Acquirer |
| --- | --- | --- | --- | --- | --- |
| **KernelBench** | a GPU kernel (CUDA/Triton) | reference op + shapes | torch reference in [repo](https://github.com/ScalingIntelligence/KernelBench) | 1×A100 | git |
| **SWE-bench Lite** | the coding-agent scaffold | base model + tasks/tests | [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) / [Agentless](https://arxiv.org/abs/2407.01489) | API | git |
| **MLE-bench (Lite)** | the ML-eng pipeline | data + budget (24h) | [AIDE](https://github.com/WecoAI/aideml) (wired in [mle-bench/agents](https://github.com/openai/mle-bench/blob/main/agents/README.md)) | 4×A100 | git |
| **AlgoTune** (more tasks) | the numerical solver | reference solver + eval | reference in [repo](https://github.com/oripress/AlgoTune) | CPU | git |
| **nanoGPT speedrun** | the training code | token/compute budget | `records/` in [modded-nanogpt](https://github.com/KellerJordan/modded-nanogpt) | 4×A100 (scaled) | git |

`algotune_knn` is already shipped (the dogfood pack); AlgoTune here = adding more tasks.

## Tier 2 — strong, API/HF (prompt / agent / RAG / reasoning angles)

| Benchmark | Angle (edit) | Frozen | Harvestable baseline | Compute | Acquirer |
| --- | --- | --- | --- | --- | --- |
| **GAIA** | a deep-research agent | base model | [Open Deep Research / smolagents](https://huggingface.co/blog/open-deep-research) | API | hf+git |
| **τ-bench** | a tool-use agent policy | base model | repo's function-calling agent ([tau-bench](https://github.com/sierra-research/tau-bench)) | API | git |
| **BIRD (text-to-SQL)** | a text→SQL pipeline | base model | [ContextualAI/bird-sql](https://github.com/ContextualAI/bird-sql) | API (+opt small model) | git+hf |
| **GSM8K / HotpotQA (DSPy)** | a prompt/pipeline | base model | [DSPy](https://dspy.ai/deep-dive/data-handling/built-in-datasets/) CoT/ReAct | API | git+hf |
| **LongBench v2 / RULER** | a long-context / RAG method | base model | retrieval baseline in repo | API | git+hf |

## Tier 3 — feasible but heavier / baseline needs building / extra env

LiveCodeBench · BigCodeBench (code-gen scaffold) · GPQA + reasoning scaffold (test-time
compute) · WebArena / OSWorld (need browser/OS env — heavy) · BEIR / MTEB (retriever / reranker,
small GPU).

## Out of scope (measure a frozen model, or need sim/hardware)

Pure multimodal/video QA · embodied VLA robotics · safety red-teaming. These evaluate a model
rather than optimizing an artifact, or need hardware/sim Arbor can't drive.

## Same dataset → multiple angle-locked packs

A dataset is not a pack; the angle is. e.g. **GSM8K** splits into:

| Pack | edit | frozen | measures |
| --- | --- | --- | --- |
| `gsm8k_prompt_opt` | prompt | model | prompt engineering |
| `gsm8k_tool_scaffold` | reasoning scaffold | model | test-time inference framework |
| `gsm8k_finetune` | training code + weights | budget | fine-tuning method |

## First moves (validate both acquirers)

Pick **one git-with-eval** + **one HF-dataset+API** so v1 exercises both `Acquirer`s end-to-end:

1. **KernelBench** (git, GPU) — the GPU analogue of `algotune_knn`; clean eval + reference
   baseline in-repo.
2. **GSM8K-via-DSPy** *or* **GPQA + scaffold** (hf+api) — data on HF, metric via API, baseline
   harvested from DSPy.

These two prove the shared spine across both modalities; the rest of each tier follows once the
pipeline is real.
