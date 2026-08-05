from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from time import perf_counter
from typing import Callable

from aisci_agent_runtime.subagents.base import SubagentOutput, SubagentStatus


@dataclass(frozen=True)
class SubagentTask:
    name: str
    run_fn: Callable[[dict[str, SubagentOutput]], SubagentOutput]
    dependencies: list[str] = field(default_factory=list)
    context_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CoordinatorResult:
    outputs: dict[str, SubagentOutput]
    synthesized_output: str
    total_runtime_seconds: float
    total_tokens: dict[str, int]
    all_success: bool
    failed_subagents: list[str]


class SubagentCoordinator:
    def __init__(
        self,
        synthesize_fn: Callable[[dict[str, SubagentOutput]], str] | None = None,
        max_workers: int | None = None,
    ) -> None:
        self.synthesize_fn = synthesize_fn or self._default_synthesize
        self.max_workers = max_workers

    def run(self, tasks: list[SubagentTask]) -> CoordinatorResult:
        started = perf_counter()
        outputs: dict[str, SubagentOutput] = {}
        total_tokens = {"input": 0, "output": 0}
        failed_subagents: list[str] = []

        for level_tasks in self._group_by_level(tasks):
            if not level_tasks:
                continue
            level_outputs = self._run_level(level_tasks, outputs)
            for task in level_tasks:
                result = level_outputs[task.name]
                outputs[task.name] = result
                for key in ("input", "output"):
                    total_tokens[key] += result.token_usage.get(key, 0)
                if result.status != SubagentStatus.COMPLETED:
                    failed_subagents.append(task.name)

        return CoordinatorResult(
            outputs=outputs,
            synthesized_output=self.synthesize_fn(outputs),
            total_runtime_seconds=perf_counter() - started,
            total_tokens=total_tokens,
            all_success=not failed_subagents,
            failed_subagents=failed_subagents,
        )

    def _run_level(
        self,
        tasks: list[SubagentTask],
        outputs: dict[str, SubagentOutput],
    ) -> dict[str, SubagentOutput]:
        if len(tasks) == 1:
            task = tasks[0]
            try:
                return {task.name: self._run_single_task(task, outputs)}
            except Exception as exc:  # noqa: BLE001
                return {
                    task.name: SubagentOutput(
                        status=SubagentStatus.FAILED,
                        content="",
                        error_message=str(exc),
                    )
                }

        max_workers = self.max_workers or len(tasks)
        results: dict[str, SubagentOutput] = {}
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(tasks)))) as executor:
            future_map = {
                executor.submit(self._run_single_task, task, outputs): task
                for task in tasks
            }
            for future in as_completed(future_map):
                task = future_map[future]
                try:
                    results[task.name] = future.result()
                except Exception as exc:  # noqa: BLE001
                    results[task.name] = SubagentOutput(
                        status=SubagentStatus.FAILED,
                        content="",
                        error_message=str(exc),
                    )
        return results

    def _run_single_task(
        self,
        task: SubagentTask,
        outputs: dict[str, SubagentOutput],
    ) -> SubagentOutput:
        context_keys = task.context_keys or task.dependencies
        context = {key: outputs[key] for key in context_keys if key in outputs}
        return task.run_fn(context)

    def _default_synthesize(self, outputs: dict[str, SubagentOutput]) -> str:
        sections: list[str] = []
        for name, output in outputs.items():
            marker = "OK" if output.status == SubagentStatus.COMPLETED else "FAILED"
            body = output.content.strip() or output.error_message or "(no output)"
            sections.append(f"## {name} [{marker}]\n\n{body}")
        return "\n\n---\n\n".join(sections)

    def _group_by_level(self, tasks: list[SubagentTask]) -> list[list[SubagentTask]]:
        task_map = {task.name: task for task in tasks}
        levels: dict[str, int] = {}
        visiting: set[str] = set()

        def get_level(name: str) -> int:
            if name in levels:
                return levels[name]
            if name in visiting:
                raise ValueError(f"Cyclic dependency detected involving '{name}'")
            visiting.add(name)
            task = task_map.get(name)
            if task is None or not task.dependencies:
                levels[name] = 0
            else:
                levels[name] = 1 + max(get_level(dep) for dep in task.dependencies)
            visiting.remove(name)
            return levels[name]

        for task in tasks:
            get_level(task.name)

        grouped: list[list[SubagentTask]] = [[] for _ in range(max(levels.values(), default=0) + 1)]
        for task in tasks:
            grouped[levels[task.name]].append(task)
        return grouped


__all__ = ["CoordinatorResult", "SubagentCoordinator", "SubagentTask"]
