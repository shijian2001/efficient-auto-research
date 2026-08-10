from __future__ import annotations

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "BenchmarkAdapters"


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative), filename=relative)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_relay_client():
    package_name = "_relay_client_static_fixture"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ADAPTERS)]
    sys.modules[package_name] = package
    for module_name in ("security", "contracts", "process"):
        _load_module(
            f"{package_name}.{module_name}", ADAPTERS / f"{module_name}.py"
        )
    relay_package_name = f"{package_name}.LLMRelay"
    relay_package = types.ModuleType(relay_package_name)
    relay_package.__path__ = [str(ADAPTERS / "LLMRelay")]
    sys.modules[relay_package_name] = relay_package
    return _load_module(
        f"{relay_package_name}.client", ADAPTERS / "LLMRelay/client.py"
    )


class UnifiedLLMRelayArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _load_module(
            "_relay_server_static_fixture", ADAPTERS / "LLMRelay/server.py"
        )
        cls.client = _load_relay_client()
        cls.registry = _load_module(
            "_relay_registry_static_fixture", ADAPTERS / "registry.py"
        )

    def test_relay_is_repository_owned_and_old_locations_are_gone(self) -> None:
        for relative in (
            "BenchmarkAdapters/LLMRelay/server.py",
            "BenchmarkAdapters/LLMRelay/supervisor.py",
            "BenchmarkAdapters/LLMRelay/client.py",
            "BenchmarkAdapters/LLMRelay/forwarder.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        for relative in (
            "BenchmarkAdapters/relay.py",
            "BenchmarkAdapters/unix_relay_forwarder.py",
            "docker-eval/llm_relay_proxy.py",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_messages_requests_convert_to_canonical_chat(self) -> None:
        converted = self.server._messages_request_to_chat(
            {
                "model": "downstream-model",
                "system": "system instruction",
                "messages": [
                    {"role": "user", "content": "run the tool"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call-1",
                                "name": "evaluate",
                                "input": {"candidate": "a"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call-1",
                                "content": "0.75",
                            }
                        ],
                    },
                ],
                "tools": [
                    {
                        "name": "evaluate",
                        "description": "score one candidate",
                        "input_schema": {"type": "object"},
                    }
                ],
            }
        )
        self.assertEqual(converted["messages"][0]["role"], "system")
        self.assertEqual(
            converted["messages"][2]["tool_calls"][0]["function"]["name"],
            "evaluate",
        )
        self.assertEqual(converted["messages"][3]["role"], "tool")
        self.assertEqual(converted["tools"][0]["type"], "function")

    def test_chat_and_responses_requests_convert_both_directions(self) -> None:
        responses = self.server._chat_request_to_responses(
            {
                "model": "model",
                "messages": [
                    {"role": "system", "content": "be concise"},
                    {"role": "user", "content": "hello"},
                ],
                "max_tokens": 64,
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "evaluate"},
                },
            }
        )
        self.assertEqual(responses["instructions"], "be concise")
        self.assertEqual(responses["input"][0]["role"], "user")
        self.assertEqual(responses["max_output_tokens"], 64)
        self.assertEqual(
            responses["tool_choice"], {"type": "function", "name": "evaluate"}
        )
        chat = self.server._responses_request_to_chat(responses)
        self.assertEqual(chat["messages"][0]["role"], "system")
        self.assertEqual(chat["messages"][1]["role"], "user")
        self.assertEqual(chat["max_tokens"], 64)
        self.assertEqual(
            chat["tool_choice"],
            {"type": "function", "function": {"name": "evaluate"}},
        )

    def test_reasoning_model_strips_incompatible_sampling_parameters(self) -> None:
        original_effort = self.server.REASONING_EFFORT
        original_parameters = self.server.FORCE_PARAMETERS
        try:
            self.server.REASONING_EFFORT = "low"
            self.server.FORCE_PARAMETERS = {
                **original_parameters,
                "logprobs": True,
                "top_logprobs": 2,
                "top_p": 0.9,
            }
            rewritten = self.server._rewrite_body(
                {
                    "model": "downstream-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "logprobs": True,
                    "top_logprobs": 2,
                    "top_p": 0.9,
                },
                "/v1/chat/completions",
            )
        finally:
            self.server.REASONING_EFFORT = original_effort
            self.server.FORCE_PARAMETERS = original_parameters
        self.assertNotIn("logprobs", rewritten)
        self.assertNotIn("top_logprobs", rewritten)
        self.assertNotIn("top_p", rewritten)

    def test_chat_response_normalizes_for_responses_and_messages_clients(self) -> None:
        chat = {
            "id": "chatcmpl-1",
            "model": "model",
            "created": 1,
            "choices": [
                {
                    "message": {"role": "assistant", "content": "answer"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
            },
        }
        responses = self.server._chat_response_to_responses(chat)
        messages = self.server._chat_response_to_messages(chat)
        self.assertEqual(responses["object"], "response")
        self.assertEqual(responses["output_text"], "answer")
        self.assertEqual(messages["type"], "message")
        self.assertEqual(messages["content"], [{"type": "text", "text": "answer"}])

    def test_upstream_dispatch_is_only_openai_chat_or_responses(self) -> None:
        tree = _tree("BenchmarkAdapters/LLMRelay/server.py")
        upstream_paths = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "_post_upstream" or not node.args:
                continue
            path = node.args[0]
            self.assertIsInstance(path, ast.Constant)
            upstream_paths.add(path.value)
        self.assertEqual(upstream_paths, {"/chat/completions", "/responses"})
        source = _source("BenchmarkAdapters/LLMRelay/server.py")
        self.assertEqual(source.count("_client().post("), 1)
        self.assertNotIn('path == "/completions"', source)
        self.assertNotIn('path == "/embeddings"', source)

    def test_agent_environment_is_fail_closed_to_local_relay(self) -> None:
        environment = self.client.relay_agent_environment(
            base_url="http://127.0.0.1:6200/v1",
            model="reviewed-model",
            environment={
                "OPENAI_API_KEY": "host-openai-secret",
                "CLAUDE_CODE_OAUTH_TOKEN": "host-claude-secret",
                "PRIVATE_PROVIDER_API_KEY": "host-private-secret",
                "OPENAI_BASE_URL": "https://upstream.invalid/v1",
                "HTTPS_PROXY": "http://upstream-proxy.invalid",
                "SAFE_SETTING": "preserved",
            },
        )
        self.assertEqual(environment["OPENAI_API_KEY"], "proxy")
        self.assertEqual(environment["UPSTREAM_API_KEY"], "proxy")
        self.assertEqual(environment["ANTHROPIC_API_KEY"], "proxy")
        self.assertEqual(environment["ANTHROPIC_AUTH_TOKEN"], "proxy")
        self.assertEqual(
            environment["OPENAI_BASE_URL"], "http://127.0.0.1:6200/v1"
        )
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:6200")
        self.assertEqual(environment["SAFE_SETTING"], "preserved")
        joined = "\n".join(environment.values())
        self.assertNotIn("host-openai-secret", joined)
        self.assertNotIn("host-claude-secret", joined)
        self.assertNotIn("host-private-secret", joined)
        self.assertNotIn("upstream.invalid", joined)
        self.assertNotIn("upstream-proxy.invalid", joined)

    def test_registry_contains_all_seven_agents(self) -> None:
        self.assertEqual(
            set(self.registry.AGENTS),
            {
                "ear",
                "mlevolve",
                "arbor",
                "codex",
                "claude-code",
                "ml-master-2",
                "ai-scientist",
            },
        )

    def test_all_five_benchmark_execution_layers_start_the_relay(self) -> None:
        mle = _source("BenchmarkAdapters/MLEBenchLite/adapter.py")
        terminal = _source("BenchmarkAdapters/TerminalAO/supervisor.py")
        autoresearch = _source("BenchmarkAdapters/AutoResearch/launchers/runner.py")
        optimizer = _source("BenchmarkAdapters/OptimizerDesign/adapter.py")
        fml = _source("BenchmarkAdapters/FMLBench/runner.py")
        docker = _source("docker-eval/run_in_docker.sh")
        self.assertIn("with relay:", mle)
        self.assertIn("BenchmarkAdapters/LLMRelay/server.py", docker)
        self.assertIn("with relay:", terminal)
        self.assertIn("with relay, self.broker_server_factory", autoresearch)
        self.assertIn("route_command_through_relay(", autoresearch)
        self.assertIn("NativeCommandSearchRunner(", optimizer)
        self.assertIn("with relay_context as relay", fml)
        for source in (mle, terminal, autoresearch, fml):
            self.assertIn("RelayProcess(", source)

    def test_shared_runners_route_without_agent_specific_exclusions(self) -> None:
        for relative, function_or_class in (
            (
                "BenchmarkAdapters/AutoResearch/launchers/runner.py",
                "NativeCommandSearchRunner",
            ),
            ("BenchmarkAdapters/TerminalAO/supervisor.py", "_run_terminal_ao_once"),
            ("BenchmarkAdapters/FMLBench/runner.py", "run_fml_task"),
        ):
            source = _source(relative)
            tree = ast.parse(source, filename=relative)
            node = next(
                item
                for item in ast.walk(tree)
                if isinstance(item, (ast.ClassDef, ast.FunctionDef))
                and item.name == function_or_class
            )
            segment = ast.get_source_segment(source, node) or ""
            self.assertIn("RelayProcess(", segment, relative)
            self.assertNotIn('if agent in {"', segment, relative)
            self.assertNotIn('if context.agent in {"', segment, relative)

    def test_formal_sandboxes_expose_only_unix_relay_on_loopback(self) -> None:
        for relative in (
            "BenchmarkAdapters/MLEBenchLite/adapter.py",
            "BenchmarkAdapters/TerminalAO/launchers/sandbox.py",
            "BenchmarkAdapters/AutoResearch/launchers/sandbox.py",
            "BenchmarkAdapters/FMLBench/sandbox.py",
        ):
            source = _source(relative)
            self.assertIn("LLMRelay/forwarder.py", source, relative)
            self.assertIn("6200", source, relative)
            self.assertTrue(
                "--unshare-all" in source or "--unshare-net" in source,
                relative,
            )
        autoresearch = _source(
            "BenchmarkAdapters/AutoResearch/launchers/sandbox.py"
        )
        for forbidden in (
            '"OPENAI_API_KEY",',
            '"UPSTREAM_API_KEY",',
            '"ANTHROPIC_API_KEY",',
            '"HTTP_PROXY",',
            '"HTTPS_PROXY",',
        ):
            self.assertNotIn(forbidden, autoresearch)

    def test_supervisor_readiness_matches_selected_upstream_api(self) -> None:
        source = _source("BenchmarkAdapters/LLMRelay/supervisor.py")
        tree = ast.parse(source)
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_check_upstream_ready"
        )
        segment = ast.get_source_segment(source, method) or ""
        self.assertIn('path = "/v1/responses"', segment)
        self.assertIn('path = "/v1/chat/completions"', segment)
        self.assertIn("self._resolved_upstream_api()", segment)


if __name__ == "__main__":
    unittest.main()
