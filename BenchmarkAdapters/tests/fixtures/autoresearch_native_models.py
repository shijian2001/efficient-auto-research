"""Deterministic no-network model boundaries for native AutoResearch bridge tests."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def candidate_source(score: float) -> str:
    return f"""class Cuda:
    def manual_seed(self, seed):
        pass
class Torch:
    cuda = Cuda()
    def manual_seed(self, seed):
        pass
torch = Torch()
torch.manual_seed(42)
torch.cuda.manual_seed(42)
print('val_bpb: {score}')
print('training_seconds: 300.0')
print('total_seconds: 301.0')
print('peak_vram_mb: 10.0')
print('mfu_percent: 1.0')
print('total_tokens_M: 1.0')
print('num_steps: 11')
print('num_params_M: 1.0')
print('depth: 1')
"""


def proposal_command() -> str:
    return f"{Path(__file__).resolve()} proposal"


def _proposal() -> int:
    request = json.loads(input())
    score = {
        "ear": 1.06,
        "mlevolve": 1.05,
    }[str(request["agent"])]
    print(
        json.dumps(
            {
                "plan": f"deterministic proposal through {request['native_scheduler']}",
                "train_source": candidate_source(score),
                "embedding": [1.0, score],
            }
        )
    )
    return 0


def _workspace() -> Path:
    value = os.environ.get("AUTORESEARCH_TEST_WORKSPACE")
    if not value:
        raise RuntimeError("AUTORESEARCH_TEST_WORKSPACE is required")
    return Path(value)


class EvoMasterScriptedLLM:
    def __init__(self, *, stage: str, seed: int) -> None:
        self.stage = stage
        self.seed = seed
        self.calls = 0
        self.evaluate_command: str | None = None

    def query(self, dialog):
        from evomaster.utils.types import AssistantMessage, FunctionCall, ToolCall

        self.calls += 1
        dialog_text = "\n".join(str(message.content or "") for message in dialog.messages)
        if self.evaluate_command is None:
            match = re.search(r"Dev evaluator: (.+)", dialog_text)
            if match is None:
                raise AssertionError("EvoMaster native prompt omitted the dev evaluator")
            self.evaluate_command = match.group(1).strip()
        if self.calls == 1:
            source = candidate_source(1.04)
            return AssistantMessage(
                content=f"editing train.py in native stage {self.stage}",
                tool_calls=[
                    ToolCall(
                        id=f"edit-{self.stage}",
                        function=FunctionCall(
                            name="execute_bash",
                            arguments=json.dumps(
                                {
                                    "command": (
                                        "python - <<'PY'\n"
                                        "from pathlib import Path\n"
                                        f"Path('train.py').write_text({source!r}, encoding='utf-8')\n"
                                        "PY"
                                    ),
                                    "is_input": "false",
                                    "timeout": 10,
                                }
                            ),
                        ),
                    )
                ],
            )
        if self.calls == 2:
            return AssistantMessage(
                content="requesting structured development feedback",
                tool_calls=[
                    ToolCall(
                        id=f"evaluate-{self.stage}",
                        function=FunctionCall(
                            name="execute_bash",
                            arguments=json.dumps(
                                {
                                    "command": self.evaluate_command,
                                    "is_input": "false",
                                    "timeout": 10,
                                }
                            ),
                        ),
                    )
                ],
            )
        if "val_bpb" not in dialog_text:
            raise AssertionError("EvoMaster native loop did not consume structured dev feedback")
        return AssistantMessage(
            content="native stage complete",
            tool_calls=[
                ToolCall(
                    id=f"finish-{self.stage}",
                    function=FunctionCall(
                        name="finish",
                        arguments=json.dumps(
                            {"message": "stage complete", "task_completed": "true"}
                        ),
                    ),
                )
            ],
        )


def evomaster_factory(*, stage: str, seed: int):
    return EvoMasterScriptedLLM(stage=stage, seed=seed)


class AiScientistScriptedLLM:
    def __init__(self, *, seed: int) -> None:
        self.seed = seed
        self.calls = 0
        self.evaluate_command: str | None = None

    def chat(self, messages, tools=None):
        from aisci_agent_runtime.llm_client import LLMResponse, ToolCallResult

        del tools
        self.calls += 1
        messages_text = json.dumps(messages, sort_keys=True)
        if self.evaluate_command is None:
            match = re.search(
                r"typed development capability command: (.+?)\. The metric",
                messages_text,
            )
            if match is None:
                raise AssertionError("AiScientist native prompt omitted the dev evaluator")
            self.evaluate_command = match.group(1).replace("\\n", " ").strip()
        if self.calls == 1:
            return LLMResponse(
                text_content="editing candidate through native AiScientist tool loop",
                tool_calls=[
                    ToolCallResult(
                        call_id="edit-1",
                        name="edit_file",
                        arguments={
                            "command": "create",
                            "path": "train.py",
                            "file_text": candidate_source(1.03),
                        },
                    )
                ],
                usage={"input": 1, "output": 1},
                raw_message=None,
            )
        if self.calls == 2:
            return LLMResponse(
                text_content="requesting structured development feedback",
                tool_calls=[
                    ToolCallResult(
                        call_id="evaluate-1",
                        name="bash",
                        arguments={"command": self.evaluate_command, "timeout": 10},
                    )
                ],
                usage={"input": 1, "output": 1},
                raw_message=None,
            )
        if "val_bpb" not in messages_text:
            raise AssertionError("AiScientist native loop did not consume structured dev feedback")
        return LLMResponse(
            text_content=None,
            tool_calls=[
                ToolCallResult(
                    call_id="done-1",
                    name="subagent_complete",
                    arguments={"content": "native AiScientist run complete"},
                )
            ],
            usage={"input": 1, "output": 1},
            raw_message=None,
        )


def ai_scientist_factory(*, seed: int):
    return AiScientistScriptedLLM(seed=seed)


class ArborScriptedProvider:
    model = "deterministic-arbor-test-provider"

    def __init__(self, *, seed: int) -> None:
        self.seed = seed
        self.calls = 0
        self.evaluate_command: str | None = None

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def create(self, *, system, messages, tools=None, max_tokens=16384):
        from arbor.core.llm.base import LLMResponse, TextBlock, ToolUseBlock, Usage

        del system, tools, max_tokens
        self.calls += 1
        messages_text = json.dumps(messages, sort_keys=True)
        if self.evaluate_command is None:
            match = re.search(
                r"Evaluate each serious candidate with: (.+?)\. The metric",
                messages_text,
            )
            if match is None:
                raise AssertionError("Arbor native prompt omitted the dev evaluator")
            self.evaluate_command = match.group(1).replace("\\n", " ").strip()
        if self.calls == 1:
            source = candidate_source(1.02)
            content = [
                ToolUseBlock(
                    id="bash-edit",
                    name="Bash",
                    input={
                        "command": (
                            "python - <<'PY'\n"
                            "from pathlib import Path\n"
                            f"Path('train.py').write_text({source!r}, encoding='utf-8')\n"
                            "PY"
                        )
                    },
                )
            ]
            return LLMResponse(
                content=content,
                stop_reason="tool_use",
                usage=Usage(input_tokens=1, output_tokens=1),
                model=self.model,
                raw_content=[
                    {"type": "tool_use", "id": "bash-edit", "name": "Bash", "input": content[0].input}
                ],
            )
        if self.calls == 2:
            content = [
                ToolUseBlock(
                    id="bash-evaluate",
                    name="Bash",
                    input={"command": self.evaluate_command},
                )
            ]
            return LLMResponse(
                content=content,
                stop_reason="tool_use",
                usage=Usage(input_tokens=1, output_tokens=1),
                model=self.model,
                raw_content=[
                    {
                        "type": "tool_use",
                        "id": "bash-evaluate",
                        "name": "Bash",
                        "input": content[0].input,
                    }
                ],
            )
        if "val_bpb" not in messages_text:
            raise AssertionError("Arbor native coordinator did not consume structured dev feedback")
        return LLMResponse(
            content=[TextBlock("native Arbor coordinator complete")],
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=1),
            model=self.model,
            raw_content=[{"type": "text", "text": "native Arbor coordinator complete"}],
        )


def arbor_factory(*, seed: int):
    return ArborScriptedProvider(seed=seed)


def main() -> int:
    if len(os.sys.argv) == 2 and os.sys.argv[1] == "proposal":
        return _proposal()
    raise SystemExit("usage: autoresearch_native_models.py proposal")


if __name__ == "__main__":
    raise SystemExit(main())
