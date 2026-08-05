from __future__ import annotations

from aisci_domain_paper.prompts import (
    ENV_SETUP_SYSTEM_PROMPT,
    EXPERIMENT_SYSTEM_PROMPT,
    EXPLORE_SYSTEM_PROMPT,
    GENERAL_SYSTEM_PROMPT,
    IMPLEMENTATION_SYSTEM_PROMPT,
    MAIN_AGENT_SYSTEM_PROMPT,
    PLAN_SYSTEM_PROMPT,
    PRIORITIZATION_SYSTEM_PROMPT,
    RESOURCE_DOWNLOAD_SYSTEM_PROMPT,
    render_implementation_system_prompt,
    render_main_agent_system_prompt,
    render_plan_system_prompt,
    render_prioritization_system_prompt,
)
from aisci_domain_paper.subagents import GeneralSubagent, subagent_class_for_kind
from aisci_domain_paper.tools import (
    build_env_setup_tools,
    build_general_tools,
    build_implementation_tools,
    build_resource_download_tools,
)


def test_main_prompt_contains_upstream_paper_workflow() -> None:
    assert "maximize the reproduction score" in MAIN_AGENT_SYSTEM_PROMPT
    assert "reproduce.sh" in MAIN_AGENT_SYSTEM_PROMPT
    assert "read_paper" in MAIN_AGENT_SYSTEM_PROMPT
    assert "prioritize_tasks" in MAIN_AGENT_SYSTEM_PROMPT
    assert "implement" in MAIN_AGENT_SYSTEM_PROMPT
    assert "run_experiment" in MAIN_AGENT_SYSTEM_PROMPT
    assert "clean_reproduce_validation" in MAIN_AGENT_SYSTEM_PROMPT
    assert "submit" in MAIN_AGENT_SYSTEM_PROMPT
    assert "finish_run" not in MAIN_AGENT_SYSTEM_PROMPT


def test_subagent_prompts_are_no_longer_placeholder_role_blurbs() -> None:
    assert "Implementation Specialist" in IMPLEMENTATION_SYSTEM_PROMPT
    assert "Experiment Agent" in EXPERIMENT_SYSTEM_PROMPT
    assert "Prioritization Strategist" in PRIORITIZATION_SYSTEM_PROMPT
    assert "Environment Setup Specialist" in ENV_SETUP_SYSTEM_PROMPT
    assert "Resource Download Specialist" in RESOURCE_DOWNLOAD_SYSTEM_PROMPT


def test_live_prompt_renderers_hide_unavailable_optional_tools() -> None:
    capabilities = {
        "online_research": {"available": False},
        "linter": {"available": True},
    }
    main_prompt = render_main_agent_system_prompt(capabilities)
    implementation_prompt = render_implementation_system_prompt(capabilities)
    prioritization_prompt = render_prioritization_system_prompt(capabilities)
    plan_prompt = render_plan_system_prompt(capabilities)

    assert "web_search" not in main_prompt
    assert "link_summary" not in implementation_prompt
    assert "github" not in implementation_prompt
    assert "parse_rubric" in prioritization_prompt
    assert "write_priorities" in prioritization_prompt
    assert "bash" not in prioritization_prompt
    assert "python" not in prioritization_prompt
    assert "write_plan" in plan_prompt
    assert "edit_file" not in plan_prompt
    assert "web_search" not in plan_prompt


def test_full_prompts_match_current_runtime_tool_contract() -> None:
    assert "web_search" in MAIN_AGENT_SYSTEM_PROMPT
    assert "link_summary" in MAIN_AGENT_SYSTEM_PROMPT
    assert "git_commit" not in GENERAL_SYSTEM_PROMPT
    assert "edit_file" not in GENERAL_SYSTEM_PROMPT
    assert "write_plan" in PLAN_SYSTEM_PROMPT
    assert "write_priorities" in PRIORITIZATION_SYSTEM_PROMPT
    assert "parse_rubric" in PRIORITIZATION_SYSTEM_PROMPT
    assert "bash" not in PRIORITIZATION_SYSTEM_PROMPT
    assert "python" not in PRIORITIZATION_SYSTEM_PROMPT
    assert "/home/agent/plan.md" not in PRIORITIZATION_SYSTEM_PROMPT
    assert "web_search" in EXPLORE_SYSTEM_PROMPT
    assert "github" not in IMPLEMENTATION_SYSTEM_PROMPT
    assert "check_env_status" in ENV_SETUP_SYSTEM_PROMPT
    assert "record_env_setup" in ENV_SETUP_SYSTEM_PROMPT
    assert "- `edit_file`:" not in ENV_SETUP_SYSTEM_PROMPT
    assert "check_download_status" in RESOURCE_DOWNLOAD_SYSTEM_PROMPT
    assert "record_download" in RESOURCE_DOWNLOAD_SYSTEM_PROMPT
    assert "- `python`:" not in RESOURCE_DOWNLOAD_SYSTEM_PROMPT
    assert "- `edit_file`:" not in RESOURCE_DOWNLOAD_SYSTEM_PROMPT
    assert 'git_commit(files=".", message=' not in EXPERIMENT_SYSTEM_PROMPT
    assert 'git_commit(message="fix: description")' in EXPERIMENT_SYSTEM_PROMPT


def test_main_prompt_mentions_upstream_guardrails_and_clean_validation_cadence() -> None:
    prompt = render_main_agent_system_prompt(
        {
            "online_research": {"available": True},
            "linter": {"available": True},
        }
    )
    impl_prompt = render_implementation_system_prompt(
        {
            "online_research": {"available": True},
            "linter": {"available": True},
        }
    )

    assert "After first `implement(mode=\"full\")`" in prompt
    assert "Use `run_experiment()` for iterative testing" in prompt
    assert "If metrics deviate > ~20% from the paper" in prompt
    assert "if 2-3 fix attempts do not close the gap, move on to the next task" in prompt
    assert "Never run more than 2 consecutive experiments without calling implement() in between." in prompt
    assert "clean validation runs `git clean -fd`" in impl_prompt
    assert 'link_summary(url=' in impl_prompt


def test_paper_prompts_include_gpu_guidance() -> None:
    assert 'python -c "import torch; print(torch.cuda.is_available())"' in MAIN_AGENT_SYSTEM_PROMPT
    assert "Always use GPU for training and computation-intensive tasks." in IMPLEMENTATION_SYSTEM_PROMPT
    assert "**NEVER** use `--device cpu` for model training" in IMPLEMENTATION_SYSTEM_PROMPT
    assert 'python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"' in IMPLEMENTATION_SYSTEM_PROMPT
    assert "If training is unexpectedly slow, check if code is accidentally using CPU" in EXPERIMENT_SYSTEM_PROMPT
    assert "do not spend more than 1-2 fix attempts on result accuracy" in EXPERIMENT_SYSTEM_PROMPT
    assert "This environment has NVIDIA GPU with CUDA pre-installed" in ENV_SETUP_SYSTEM_PROMPT


def test_tool_builders_expose_aligned_paper_subagent_tools() -> None:
    research_capabilities = {"online_research": {"available": True}}

    implementation_tools = {tool.name() for tool in build_implementation_tools(research_capabilities)}
    general_tools = {tool.name() for tool in build_general_tools(research_capabilities)}
    env_setup_tools = {tool.name() for tool in build_env_setup_tools()}
    resource_tools = {tool.name() for tool in build_resource_download_tools(research_capabilities)}

    assert {"edit_file", "git_commit", "web_search", "link_summary"} <= implementation_tools
    assert "edit_file" not in general_tools
    assert "git_commit" not in general_tools
    assert {"check_env_status", "record_env_setup"} <= env_setup_tools
    assert "edit_file" not in env_setup_tools
    assert {"check_download_status", "record_download"} <= resource_tools
    assert "python" not in resource_tools
    assert "edit_file" not in resource_tools
    assert "web_search" not in resource_tools
    assert "link_summary" not in resource_tools


def test_generic_alias_maps_to_general_subagent() -> None:
    assert subagent_class_for_kind("generic") is GeneralSubagent
