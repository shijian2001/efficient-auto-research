# Skills

Skills are markdown **playbooks** that the agent loads on demand at the moment they're
relevant. Where a [plugin](plugins.md) declares *what* to optimize, a Skill shapes *how*
the agent thinks at a specific step — for example, how to draft research ideas or how to
probe a problem from first principles.

!!! abstract "Skill vs. Plugin in one line"
    A **Skill** sharpens *how the agent reasons* at one step (a markdown checklist loaded
    when it's relevant). A [plugin](plugins.md) describes *what to optimize* for a whole
    domain. They compose — use a Skill alone, a plugin alone, or both.

!!! question "Do I need to write one?"
    **No.** Arbor ships with sensible default Skills that load automatically. Write your own
    only when you want to improve the agent's reasoning at a particular step.

## Why Skills

LLM-driven research has predictable failure modes: skipping the thinking and jumping to
plausible-sounding tweaks, reconstructing context from memory instead of reading state,
proposing parameter changes instead of real mechanisms. A Skill is a concentrated dose of
guidance that counteracts a specific failure mode — injected exactly when it matters, not
buried in a giant system prompt.

## The Skill format

A Skill is a markdown file with YAML front matter plus the instructions themselves:

```markdown
---
name: idea_drafting
description: Structured idea-drafting workflow for IDEATE rounds.
when_to_apply: At the start of every IDEATE round, BEFORE drafting any candidate idea.
---

# SKILL: Idea Drafting

You are about to enter IDEATE. Read this once now. Apply every part before
you propose a single candidate...
```

| Field | Purpose |
| --- | --- |
| `name` | Identifier used to register and reference the Skill. |
| `description` | One-line summary of what the Skill does. |
| `when_to_apply` | The trigger condition — when the agent should load and follow it. |
| *body* | The actual playbook the agent follows. |

## Bundled Skills

Arbor ships a small set of Skills out of the box, loaded automatically:

| Skill | When it applies |
| --- | --- |
| `idea_drafting` | At the start of every IDEATE round, before drafting candidate ideas. Enforces the "mechanism, not knob" bar — real research directions over parameter tweaks. |
| `first_principles_probe` | When the agent should reason about a problem from first principles rather than pattern-matching to familiar solutions. |

You can adjust which Skills are active for a single run from the intake chat — type `/` to
use a slash command:

```text
/skill load my_skill            # load one of your own Skills for this run
/skill unload first_principles_probe   # drop a default Skill this run
/skill reset                    # restore the defaults
```

## Writing your own Skill

1. Create the folder `.arbor/skills/` inside your project and add a markdown file
   there, e.g. `<project>/.arbor/skills/my_skill.md`. Arbor discovers project
   Skills from this folder; a project Skill with the same `name` overrides a bundled one.
2. Add the `name`, `description`, and `when_to_apply` front matter.
3. Write the playbook. Be concrete and prescriptive — a Skill is most effective when it
   gives the agent a checklist to apply, not vague encouragement.

Load it with `/skill load my_skill` in the chat (or rely on `when_to_apply` to trigger it
automatically).

!!! tip "Skills vs. plugins"
    Reach for a **plugin** to define the eval contract, protected paths, and budgets for a
    domain. Reach for a **Skill** to improve the agent's reasoning at a particular step.
    They compose: a domain plugin can pair with Skills that sharpen ideation for that
    domain.
