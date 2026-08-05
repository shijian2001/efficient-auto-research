# Roadmap

This document records the directions we plan to push Arbor in: a few categories of open
problems and our current preliminary thinking on each. These problems and ideas will be
adjusted, merged, or dropped as the team's understanding deepens — contributions and
suggestions are welcome.

## Direction 1 — Core capability

### 1. Better long-run process management

Arbor currently maintains the entire hypothesis tree with a single Coordinator, which can
degrade over longer research horizons. We plan to improve its context management so that
the key information accumulated during a long run isn't gradually distorted or lost.

### 2. Idea quality assessment

The novelty and feasibility of a proposed idea currently rest mainly on the base model's
own ability, with no explicit external feedback to supervise it. We are considering
introducing a critic-style role that gives candidate ideas an independent assessment, to
raise the quality of this step.

### 3. Self-evolution

Arbor currently optimizes around a single benchmark, and the experience it gains is hard
to transfer to similar or same-domain tasks. We plan to add the ability to export research
trajectories, and on top of that to extract and consolidate reusable skills and experience.

### 4. Support for composite optimization objectives

Arbor's optimization objective is currently limited to a scalar score. We are considering
support for more composite objective forms — such as rubric- and LLM-judge-based scoring
and multi-objective optimization — and the comparability and anti-gaming problems such
objectives bring.

### 5. Stronger agentic research ability

Let the agent autonomously gather more experiment information as a run progresses, rather
than being confined to a pre-defined metric, giving subsequent decisions a fuller basis.

## Direction 2 — External resources

### 1. Extend to more diverse long-horizon optimization settings

The current task format assumes static optimization (a fixed, stateless environment). We
plan to add tasks that carry environment state, to support optimizing agent harnesses in
concrete settings such as office work.

### 2. Automated environment collection

Collect and grow a more comprehensive set of benchmark scenarios, covering a range of LLM
and agent evaluation benchmarks, so that Arbor can support a wider range of optimization
settings.

## Direction 3 — User experience

### 1. More worked examples and scenarios

The zero-install demo has shipped, but `algotune_knn` is still the only example a newcomer
can run end to end. We plan to grow it into a small examples gallery covering different
task types and audiences (e.g. Kaggle / MLE, prompt and harness engineering, small-scale
training), each with a copy-pasteable command and a short recording, keeping the barrier to
"runs in minutes on a laptop or a free API key".

### 2. Better export and presentation of a single run

There's already `REPORT.md`, HTML export, a live dashboard, and a read-only WebUI — what's
missing is understanding and comparing a single run. We plan to support comparison across
runs (diff multiple runs of the same task, or compare idea trees across models / providers),
add citations (the sources behind each idea) and a cost breakdown to the report, and give
the benchmark collection a reproducibility view where every entry carries a command you can
re-run directly.

---

Have an idea, or want to own one of these threads? Open a
[discussion](https://github.com/RUC-NLPIR/Arbor/discussions) or see
[Contributing](contributing.md).
