"""Thin Agent layer over the shared Optimizer Design benchmark contract."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ...AutoResearch.launchers import NativeLaunchRequest, build_native_command
from ...contracts import CommandSpec
from ...task_specs import task_spec_digest, task_spec_text
from ...thin_registry import backend_identity


@dataclass(frozen=True)
class OptimizerDesignAgentAdapter:
    agent: str
    native_component: str

    def task_environment(
        self,
        model_environment,
        agent_variant: str = "default",
    ) -> dict[str, str]:
        native_component = backend_identity(
            self.agent, "optimizer-design", agent_variant
        )
        return {
            **model_environment,
            "OPTIMIZATION_TASK_NAME": "modded-NanoGPT Track 3 Optimizer Design",
            "OPTIMIZATION_ARTIFACT_NAME": "train_gpt_simple.py",
            "OPTIMIZATION_PROGRAM_NAME": "program.md",
            "OPTIMIZATION_STATE_NAME": ".optimizer-design-candidate.json",
            "OPTIMIZATION_METRIC_NAME": "score_steps",
            "OPTIMIZATION_METRIC_DIRECTION": "minimize",
            "OPTIMIZATION_NATIVE_COMPONENT": native_component,
            "OPTIMIZATION_TASK_INSTRUCTION": (
                task_spec_text("optimizer-design")
            ),
            "BENCHMARK_TASK_SPEC_SHA256": task_spec_digest("optimizer-design"),
        }

    def bind(self, request: NativeLaunchRequest) -> NativeLaunchRequest:
        environment = self.task_environment(
            request.model_environment, request.agent_variant
        )
        return replace(request, agent=self.agent, model_environment=environment)

    def build_command(self, request: NativeLaunchRequest) -> CommandSpec:
        command = build_native_command(self.bind(request))
        return CommandSpec(
            argv=command.argv,
            cwd=command.cwd,
            env=command.env,
            timeout_seconds=command.timeout_seconds,
            label=f"Optimizer Design / {self.agent}: {command.label}",
            inherit_env=command.inherit_env,
            artifact_path=command.artifact_path,
        )


__all__ = ["OptimizerDesignAgentAdapter"]
