# Provenance — TODO_pack_name

This card is for humans. Fill in every section; a maintainer reads it before the benchmark
is accepted. (`arbor benchmark verify` checks these headings are present.)

## Source

Where the benchmark comes from — paper, repo, or competition, with a link, and how the data
was collected or generated.

## Setup & environment

Hardware (CPU / GPU), Python version, install command, env vars, and any API keys, downloads,
or services the user must provision. State whether it's offline. License of the code and data,
and whether the data may be redistributed.

## Baseline

How the shipped baseline works and what score it tends to produce. **Results vary** by user,
hardware, and (for API tasks) model — note the range you saw rather than a single fixed number.

## Contamination assessment

**Mandatory.** Could the test split be in a model's pre-training data? Is the held-out split
truly disjoint from dev? Explain why a high score reflects real capability.

## Caveats

Known limitations — hardware sensitivity, metric noise, scope.
