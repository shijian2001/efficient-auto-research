from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "BenchmarkAdapters"


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative), filename=relative)


def _function_source(relative: str, name: str) -> str:
    source = _source(relative)
    tree = ast.parse(source, filename=relative)
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    segment = ast.get_source_segment(source, node)
    if segment is None:
        raise AssertionError(f"could not locate source for {relative}:{name}")
    return segment


def _class_source(relative: str, name: str) -> str:
    source = _source(relative)
    tree = ast.parse(source, filename=relative)
    node = next(
        item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == name
    )
    segment = ast.get_source_segment(source, node)
    if segment is None:
        raise AssertionError(f"could not locate source for {relative}:{name}")
    return segment


def _load_static_modules():
    package_name = "_thin_adapter_static_fixture"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ADAPTERS)]
    sys.modules[package_name] = package

    loaded = {}
    for module_name in ("contracts", "registry", "thin_registry", "arbor_thin"):
        qualified = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(
            qualified, ADAPTERS / f"{module_name}.py"
        )
        if spec is None or spec.loader is None:
            raise AssertionError(f"could not load {module_name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        loaded[module_name] = module
    return (
        loaded["contracts"],
        loaded["registry"],
        loaded["thin_registry"],
        loaded["arbor_thin"],
    )


class ThinAdapterArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contracts, cls.registry, cls.thin, cls.arbor_thin = _load_static_modules()

    def test_registry_has_exact_fifteen_cell_matrix(self) -> None:
        expected = {
            "arbor": {
                "mle-bench-lite": "unsupported",
                "terminal-bench-ao": "official-extension-thin",
                "autoresearch-architecture": "official-extension-thin",
                "optimizer-design": "official-extension-thin",
                "fml-bench": "official-extension-thin",
            },
            "ai-scientist": {
                "mle-bench-lite": "native-thin",
                "terminal-bench-ao": "unsupported",
                "autoresearch-architecture": "unsupported",
                "optimizer-design": "unsupported",
                "fml-bench": "unsupported",
            },
            "ml-master-2": {
                "mle-bench-lite": "native-thin",
                "terminal-bench-ao": "unsupported",
                "autoresearch-architecture": "unsupported",
                "optimizer-design": "unsupported",
                "fml-bench": "unsupported",
            },
        }
        self.assertEqual(self.thin.THIN_CLASSIFICATIONS, expected)
        self.assertEqual(sum(map(len, expected.values())), 15)

    def test_cell_matrix_marks_explicit_variants_without_enabling_fallback(self) -> None:
        expected = {
            "arbor": {
                "mle-bench-lite": "patched-variant",
                "terminal-bench-ao": "official-extension-thin",
                "autoresearch-architecture": "official-extension-thin",
                "optimizer-design": "official-extension-thin",
                "fml-bench": "official-extension-thin",
            },
            "ai-scientist": {
                "mle-bench-lite": "native-thin",
                "terminal-bench-ao": "patched-variant",
                "autoresearch-architecture": "patched-variant",
                "optimizer-design": "patched-variant",
                "fml-bench": "patched-variant",
            },
            "ml-master-2": {
                "mle-bench-lite": "native-thin",
                # No variant covers Terminal AO: ML-Master 2.0's Kaggle-shaped
                # workspace cannot express Harness Engineering AO candidates.
                "terminal-bench-ao": "unsupported",
                "autoresearch-architecture": "patched-variant",
                "optimizer-design": "patched-variant",
                "fml-bench": "patched-variant",
            },
        }
        self.assertEqual(self.thin.CELL_CLASSIFICATIONS, expected)

    def test_original_and_variant_registries_are_disjoint(self) -> None:
        expected_variants = {
            "arbor-benchmark-patched",
            "ai-scientist-terminal-variant",
            "ai-scientist-architecture-variant",
            "ml-master-autoresearch-variant",
        }
        self.assertEqual(set(self.thin.AGENT_VARIANTS), expected_variants)
        self.assertTrue(expected_variants.isdisjoint(self.registry.AGENTS))

    def test_unsupported_original_never_falls_back(self) -> None:
        unsupported = (
            ("arbor", "mle-bench-lite"),
            ("ai-scientist", "terminal-bench-ao"),
            ("ai-scientist", "autoresearch-architecture"),
            ("ai-scientist", "optimizer-design"),
            ("ai-scientist", "fml-bench"),
            ("ml-master-2", "terminal-bench-ao"),
            ("ml-master-2", "autoresearch-architecture"),
            ("ml-master-2", "optimizer-design"),
            ("ml-master-2", "fml-bench"),
        )
        for agent, benchmark in unsupported:
            with self.subTest(agent=agent, benchmark=benchmark):
                with self.assertRaises(self.contracts.UnsupportedAdapterError):
                    self.thin.require_thin_support(agent, benchmark, "default")

    def test_variants_require_explicit_compatible_ids(self) -> None:
        variant = self.thin.require_thin_support(
            "ai-scientist",
            "fml-bench",
            "ai-scientist-terminal-variant@" + "a" * 40,
        )
        self.assertEqual(variant.key, "ai-scientist-terminal-variant")
        with self.assertRaises(self.contracts.AdapterError):
            self.thin.require_thin_support(
                "arbor", "fml-bench", "ai-scientist-terminal-variant"
            )
        with self.assertRaises(self.contracts.UnsupportedAdapterError):
            self.thin.require_thin_support(
                "ai-scientist", "optimizer-design", "ai-scientist-terminal-variant"
            )

    def test_original_pin_is_allowed_but_unknown_reviewed_variant_fails(self) -> None:
        self.assertIsNone(
            self.thin.require_thin_support(
                "arbor",
                "fml-bench",
                "arbor@" + self.thin.UPSTREAM_REVISIONS["arbor"],
            )
        )
        with self.assertRaises(self.contracts.AdapterError):
            self.thin.require_thin_support(
                "arbor", "fml-bench", "arbor@" + "a" * 40
            )
        with self.assertRaises(self.contracts.AdapterError):
            self.thin.require_thin_support(
                "arbor", "fml-bench", "arbor-benchmark-patchd@" + "a" * 40
            )

    def test_arbor_original_uses_official_cli_and_config(self) -> None:
        source = _function_source(
            "BenchmarkAdapters/AutoResearch/launchers/common.py",
            "build_native_command",
        )
        self.assertIn('require_clean_upstream_source("arbor")', source)
        self.assertIn("write_arbor_config", source)
        self.assertIn("eval_command=eval_command", source)
        self.assertIn('f"{{cwd}}/{contract.artifact_name}"', source)
        self.assertIn('"run"', source)
        self.assertIn('"--config"', source)
        canonical_branch = source.split("if variant is None:", 1)[1].split(
            "return _python_command", 1
        )[0]
        self.assertNotIn('"--max-cycles"', canonical_branch)
        self.assertNotIn('"--max-turns"', canonical_branch)

    def test_arbor_official_plugin_contains_no_capability_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.arbor_thin.write_arbor_config(
                root / "arbor.yaml",
                model="model-id",
                base_url="http://relay.invalid/v1",
                model_parameters={"reasoning_effort": "high"},
                eval_command='python dev.py --cwd "{cwd}" --token "$TOKEN"',
                metric_direction="minimize",
                artifact_name="train.py",
                protected_paths=("program.md",),
                required_outputs=("train.py",),
            )
            plugin = root / "plugins/benchmark_dev.yaml"
            self.assertTrue(config.is_file())
            self.assertTrue(plugin.is_file())
            plugin_text = plugin.read_text(encoding="utf-8")
            self.assertIn('"eval_cmd"', plugin_text)
            self.assertIn("{cwd}", plugin_text)
            self.assertIn("$TOKEN", plugin_text)
            self.assertNotIn("capability-secret", plugin_text)
            config_payload = __import__("json").loads(config.read_text(encoding="utf-8"))
            self.assertEqual(config_payload["llm"]["provider"], "openai-responses")
            chat_config = self.arbor_thin.write_arbor_config(
                root / "arbor-chat.yaml",
                model="chat-model",
                base_url="http://relay.invalid/v1",
                model_parameters={"use_completion_api": True},
            )
            chat_payload = __import__("json").loads(
                chat_config.read_text(encoding="utf-8")
            )
            self.assertEqual(chat_payload["llm"]["provider"], "openai-chat")

    def test_worktree_snapshot_clients_are_transport_only(self) -> None:
        for relative in (
            "BenchmarkAdapters/TerminalAO/dev_client.py",
            "BenchmarkAdapters/FMLBench/dev_client.py",
        ):
            source = _source(relative)
            self.assertIn("base64.b64encode", source)
            self.assertIn("--candidate-root", source)
            for forbidden in (
                "select_best",
                "best_by_score",
                "promote_candidate",
                "update_reward",
            ):
                self.assertNotIn(forbidden, source)

    def test_arbor_original_does_not_import_patched_core(self) -> None:
        files = (
            "BenchmarkAdapters/arbor_thin.py",
            "BenchmarkAdapters/AutoResearch/launchers/common.py",
            "BenchmarkAdapters/TerminalAO/launchers/common.py",
        )
        for relative in files:
            with self.subTest(path=relative):
                tree = _tree(relative)
                imports = {
                    node.module or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                }
                self.assertFalse(
                    any("coordinator.orchestrator" in value for value in imports)
                )
        canonical_fml = _class_source(
            "BenchmarkAdapters/FMLBench/agents/arbor.py", "ArborFMLAdapter"
        )
        self.assertNotIn("CoordinatorOrchestrator", canonical_fml)

    def test_fml_arbor_evidence_hashes_generated_plugin(self) -> None:
        source = _function_source(
            "BenchmarkAdapters/FMLBench/agents/arbor.py",
            "_generated_config_digests",
        )
        self.assertIn('plugins/benchmark_dev.yaml', source)

    def test_arbor_patched_variant_is_separate_and_not_default(self) -> None:
        variant = self.thin.AGENT_VARIANTS["arbor-benchmark-patched"]
        self.assertEqual(variant.base_agent, "arbor")
        self.assertEqual(set(variant.benchmarks), set(self.thin.BENCHMARK_IDS))
        self.assertIsNone(
            self.thin.selected_variant("arbor", "fml-bench", "default")
        )
        self.assertEqual(
            self.thin.backend_identity(
                "arbor", "fml-bench", "arbor-benchmark-patched"
            ),
            "native-arbor-coordinator",
        )

    def test_every_agent_final_selection_is_agent_declared(self) -> None:
        autoresearch = _function_source(
            "BenchmarkAdapters/AutoResearch/supervisor.py",
            "_run_autoresearch_once",
        )
        terminal = _function_source(
            "BenchmarkAdapters/TerminalAO/supervisor.py", "_run_terminal_ao_once"
        )
        optimizer = _function_source(
            "BenchmarkAdapters/OptimizerDesign/adapter.py", "_run_attested"
        )
        self.assertIn(
            "broker.scored(declaration) if declaration is not None else None",
            autoresearch,
        )
        self.assertIn("best = broker.declared", terminal)
        self.assertIn("broker.scored(declaration) if declaration else None", optimizer)
        for source in (autoresearch, terminal, optimizer):
            # The harness must never pick a candidate on the Agent's behalf, and
            # no Agent may be special-cased out of declaring its own submission.
            self.assertNotIn("broker.best", source)
            self.assertNotIn("canonical_arbor", source)
            self.assertIn('"selection_policy_id": "agent-declared"', source)

    def test_ai_scientist_original_uses_official_mle_entrypoint(self) -> None:
        source = _function_source(
            "BenchmarkAdapters/MLEBenchLite/adapter.py", "_ai_scientist_command"
        )
        for required in (
            'require_clean_upstream_source("ai-scientist")',
            '"--llm-profile-file"',
            '"mle"',
            '"run"',
            '"--llm-profile"',
        ):
            self.assertIn(required, source)
        self.assertNotIn("TerminalTaskSubagent", source)
        self.assertNotIn("ArchitectureDesignSubagent", source)

    def test_ai_scientist_original_excludes_custom_subagents_and_runtime_patches(self) -> None:
        canonical_files = (
            "BenchmarkAdapters/MLEBenchLite/adapter.py",
            "BenchmarkAdapters/FMLBench/agents/ai_scientist.py",
        )
        forbidden = (
            "TerminalTaskSubagent",
            "ArchitectureDesignSubagent",
            "LLMCancelledError",
            "retry_budget",
            "cancel_check",
            "complete_with_best",
        )
        mle_source = _function_source(canonical_files[0], "_ai_scientist_command")
        fml_tree = _tree(canonical_files[1])
        fml_imported_names = {
            alias.name
            for node in ast.walk(fml_tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        for value in forbidden:
            self.assertNotIn(value, mle_source)
            self.assertNotIn(value, fml_imported_names)

    def test_ai_scientist_custom_agents_are_explicit_variants(self) -> None:
        terminal_sources = (
            _source("BenchmarkAdapters/TerminalAO/launchers/ai_scientist.py"),
            _source(
                "BenchmarkAdapters/FMLBench/agents/ai_scientist_terminal_variant.py"
            ),
        )
        architecture_source = _source(
            "BenchmarkAdapters/AutoResearch/launchers/ai_scientist.py"
        )
        self.assertTrue(all("TerminalTaskSubagent" in value for value in terminal_sources))
        self.assertIn("ArchitectureDesignSubagent", architecture_source)
        self.assertIn("complete_with_best", architecture_source)
        self.assertIn("ai-scientist-terminal-variant", self.thin.AGENT_VARIANTS)
        self.assertIn("ai-scientist-architecture-variant", self.thin.AGENT_VARIANTS)

    def test_ml_master_original_uses_full_official_workflow(self) -> None:
        source = _function_source(
            "BenchmarkAdapters/MLEBenchLite/adapter.py", "_ml_master_command"
        )
        tree = ast.parse(source)
        self.assertIn('"run.py"', source)
        self.assertIn('"--agent"', source)
        self.assertIn('"ml_master_2"', source)
        self.assertIn('require_clean_upstream_source("ml-master-2")', source)
        self.assertFalse(any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(tree)))
        self.assertNotIn("BaseAgent", source)

    def test_ml_master_original_does_not_select_or_rank_candidates(self) -> None:
        adapter = _function_source(
            "BenchmarkAdapters/MLEBenchLite/adapter.py", "_ml_master_command"
        )
        wrapper = _function_source(
            "BenchmarkAdapters/MLEBenchLite/native_wrappers.py", "run_ml_master"
        )
        for source in (adapter, wrapper):
            self.assertNotIn("select_best", source)
            self.assertNotIn("best_by_score", source)
            self.assertNotIn("promote_candidate", source)
            self.assertNotIn("update_reward", source)
        self.assertIn("best_submission/submission.csv", wrapper)

    def test_ml_master_custom_stage_workflow_is_an_explicit_variant(self) -> None:
        sources = (
            _source("BenchmarkAdapters/AutoResearch/launchers/ml_master_2.py"),
            _source(
                "BenchmarkAdapters/FMLBench/agents/ml_master_autoresearch_variant.py"
            ),
        )
        self.assertTrue(all("for stage_name" in value for value in sources))
        variant = self.thin.AGENT_VARIANTS["ml-master-autoresearch-variant"]
        self.assertEqual(variant.base_agent, "ml-master-2")
        self.assertNotIn("mle-bench-lite", variant.benchmarks)
        # Terminal AO is excluded by architecture, not merely unimplemented: the
        # launcher is a fail-closed stub and no variant may re-enable it.
        self.assertNotIn("terminal-bench-ao", variant.benchmarks)
        terminal_stub = _source("BenchmarkAdapters/TerminalAO/launchers/ml_master_2.py")
        self.assertNotIn("for stage_name", terminal_stub)
        self.assertIn("UnsupportedAdapterError", terminal_stub)

    def test_ml_master_generated_config_preserves_workflow_controls(self) -> None:
        source = _source("BenchmarkAdapters/MLEBenchLite/ml_master_config_worker.py")
        self.assertNotIn('local["parallel"]', source)
        self.assertNotIn('local["timeout"]', source)
        self.assertNotIn('payload["grading_servers"]', source)
        self.assertNotIn("reasoning_effort", source)
        self.assertIn('upstream_model_parameters["model"] = args.model', source)

    def test_original_paths_require_reviewed_clean_upstream_sources(self) -> None:
        expected = {"arbor", "ai-scientist", "ml-master-2"}
        self.assertEqual(set(self.thin.UPSTREAM_REVISIONS), expected)
        preflight = _function_source(
            "BenchmarkAdapters/formal_preflight.py", "collect_formal_preflight"
        )
        self.assertIn("require_clean_upstream_source(agent_id)", preflight)
        gate = _function_source(
            "BenchmarkAdapters/thin_registry.py", "require_clean_upstream_source"
        )
        self.assertIn("Patched sources never satisfy the original ID", gate)

    def test_shared_original_paths_do_not_promote_merge_or_update_reward(self) -> None:
        canonical_sources = (
            _function_source(
                "BenchmarkAdapters/MLEBenchLite/adapter.py", "_ai_scientist_command"
            ),
            _function_source(
                "BenchmarkAdapters/MLEBenchLite/adapter.py", "_ml_master_command"
            ),
            _class_source(
                "BenchmarkAdapters/FMLBench/agents/arbor.py", "ArborFMLAdapter"
            ),
            _source("BenchmarkAdapters/FMLBench/agents/ai_scientist.py"),
            _source("BenchmarkAdapters/FMLBench/agents/ml_master.py"),
        )
        for source in canonical_sources:
            for forbidden in (
                "promote_candidate",
                "merge_candidate",
                "update_reward",
                "best_by_score",
                "select_best",
                "complete_with_best",
            ):
                self.assertNotIn(forbidden, source)

    def test_fml_dispatch_checks_support_before_canonical_lookup(self) -> None:
        source = _function_source(
            "BenchmarkAdapters/FMLBench/agents/__init__.py",
            "get_fml_agent_adapter",
        )
        support_index = source.index("require_thin_support")
        canonical_index = source.index("FML_AGENT_ADAPTERS")
        self.assertLess(support_index, canonical_index)

    def test_original_backend_registry_never_names_variant_implementations(self) -> None:
        for agent in ("arbor", "ai-scientist", "ml-master-2"):
            spec = self.registry.AGENTS[agent]
            values = (
                spec.mle_backend,
                spec.autoresearch_backend,
                spec.optimizer_design_backend,
                spec.terminal_ao_backend,
            )
            self.assertFalse(
                any("variant" in value or "patched" in value for value in values)
            )


if __name__ == "__main__":
    unittest.main()
