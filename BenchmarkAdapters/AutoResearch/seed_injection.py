"""Host-owned deterministic seed injection for Autoresearch reconstruction runs."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..protocol import canonical_json


_SUPPORTED_CALLS = {
    "torch.manual_seed",
    "torch.cuda.manual_seed",
    "torch.cuda.manual_seed_all",
}
_FORBIDDEN_SEED_ATTRIBUTES = {
    "manual_seed",
    "manual_seed_all",
    "seed",
    "seed_all",
    "set_rng_state",
    "set_rng_state_all",
}
_FORBIDDEN_REFLECTION_ATTRIBUTES = {
    "__import__",
    "__builtins__",
    "__class__",
    "__closure__",
    "__code__",
    "__delattr__",
    "__dict__",
    "__getattribute__",
    "__globals__",
    "__mro__",
    "__setattr__",
    "__subclasses__",
    "_exit",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "locals",
    "modules",
    "setattr",
    "sys",
    "vars",
}
_FORBIDDEN_REFLECTION_CALLS = {
    "__import__",
    "builtins.__import__",
    "compile",
    "builtins.compile",
    "delattr",
    "builtins.delattr",
    "eval",
    "builtins.eval",
    "exec",
    "builtins.exec",
    "getattr",
    "builtins.getattr",
    "globals",
    "builtins.globals",
    "locals",
    "builtins.locals",
    "os._exit",
    "setattr",
    "builtins.setattr",
    "vars",
    "builtins.vars",
}
_FORBIDDEN_REFLECTION_MODULES = {
    "builtins",
    "ctypes",
    "importlib",
    "inspect",
    "marshal",
    "operator",
    "pickle",
    "runpy",
    "sys",
    "types",
}


def _call_name(node: ast.Call) -> str | None:
    parts: list[str] = []
    current: ast.AST = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


@dataclass(frozen=True)
class SeedPolicy:
    protocol_id: str
    dev_seed: int
    held_out_seeds: tuple[int, int]
    supported_calls: tuple[str, ...] = tuple(sorted(_SUPPORTED_CALLS))

    def validate(self) -> None:
        values = (self.dev_seed, *self.held_out_seeds)
        if len(set(values)) != 3 or any(value < 0 for value in values):
            raise AdapterError("Autoresearch dev/final seeds must be three unique non-negative integers")
        if set(self.supported_calls) != _SUPPORTED_CALLS:
            raise AdapterError("Autoresearch seed policy supported calls differ from implementation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "dev_seed": self.dev_seed,
            "held_out_seeds": list(self.held_out_seeds),
            "supported_calls": list(self.supported_calls),
            "injection_policy": "replace literal arguments of required torch seed calls in evaluator copy",
            "artifact_unchanged": True,
        }

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    @classmethod
    def load(cls, path: Path) -> "SeedPolicy":
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = payload.pop("policy_digest", None)
        policy = cls(
            protocol_id=str(payload["protocol_id"]),
            dev_seed=int(payload["dev_seed"]),
            held_out_seeds=tuple(int(value) for value in payload["held_out_seeds"]),
            supported_calls=tuple(payload["supported_calls"]),
        )
        policy.validate()
        if expected != policy.digest:
            raise AdapterError(f"Autoresearch seed policy digest mismatch: {path}")
        return policy


class _SeedTransformer(ast.NodeTransformer):
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.replaced: set[str] = set()

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        name = _call_name(node)
        if name in {"getattr", "builtins.getattr", "setattr", "builtins.setattr"} and len(node.args) >= 2:
            attribute = node.args[1]
            if isinstance(attribute, ast.Constant) and attribute.value in _FORBIDDEN_SEED_ATTRIBUTES:
                raise AdapterError("Autoresearch candidate may not replace evaluator seed hooks")
        attribute = name.rsplit(".", 1)[-1] if name is not None else None
        if attribute in _FORBIDDEN_SEED_ATTRIBUTES and name not in _SUPPORTED_CALLS:
            raise AdapterError(f"Autoresearch candidate uses an unsupported seed override: {name}")
        if name in _SUPPORTED_CALLS:
            if not node.args:
                raise AdapterError(f"Autoresearch seed call has no positional argument: {name}")
            node.args[0] = ast.Constant(self.seed)
            self.replaced.add(name)
        return node

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr in _FORBIDDEN_SEED_ATTRIBUTES:
                raise AdapterError("Autoresearch candidate may not replace evaluator seed hooks")
        return self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        target = node.target
        if isinstance(target, ast.Attribute) and target.attr in _FORBIDDEN_SEED_ATTRIBUTES:
            raise AdapterError("Autoresearch candidate may not replace evaluator seed hooks")
        return self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        if any(alias.name in _FORBIDDEN_SEED_ATTRIBUTES for alias in node.names):
            raise AdapterError("Autoresearch candidate may not import alternate seed hooks")
        return self.generic_visit(node)


class _CandidatePolicyValidator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.evaluate_imports = 0
        self.evaluate_calls = 0

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in _FORBIDDEN_REFLECTION_MODULES or root == "prepare":
                raise AdapterError(f"Autoresearch candidate imports a forbidden module: {root}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if root in _FORBIDDEN_REFLECTION_MODULES:
            raise AdapterError(f"Autoresearch candidate imports a forbidden module: {root}")
        if any(alias.name in _FORBIDDEN_REFLECTION_ATTRIBUTES for alias in node.names):
            raise AdapterError("Autoresearch candidate imports a forbidden reflection helper")
        if root == "prepare" and any(alias.name == "evaluate_bpb" for alias in node.names):
            self.evaluate_imports += 1
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FORBIDDEN_REFLECTION_ATTRIBUTES:
            raise AdapterError(f"Autoresearch candidate uses forbidden reflection: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        if name in _FORBIDDEN_REFLECTION_CALLS:
            raise AdapterError(f"Autoresearch candidate uses forbidden reflection call: {name}")
        if isinstance(node.func, ast.Name) and node.func.id == "evaluate_bpb":
            self.evaluate_calls += 1
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and "AUTORESEARCH_HOST_VAL_BPB" in node.value:
            raise AdapterError("Autoresearch candidate may not emit host attestation markers")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(isinstance(target, ast.Name) and target.id == "evaluate_bpb" for target in node.targets):
            raise AdapterError("Autoresearch candidate may not replace evaluate_bpb")
        if any(
            isinstance(target, ast.Attribute) and target.attr == "evaluate_bpb"
            for target in node.targets
        ):
            raise AdapterError("Autoresearch candidate may not replace evaluate_bpb")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.target.id == "evaluate_bpb":
            raise AdapterError("Autoresearch candidate may not replace evaluate_bpb")
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if isinstance(node.target, ast.Name) and node.target.id == "evaluate_bpb":
            raise AdapterError("Autoresearch candidate may not replace evaluate_bpb")
        self.generic_visit(node)

def inject_seed(source: str, seed: int) -> str:
    if seed < 0:
        raise AdapterError("Autoresearch evaluator seed must be non-negative")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AdapterError("Autoresearch candidate train.py is not valid Python") from exc
    transformer = _SeedTransformer(seed)
    transformed = transformer.visit(tree)
    required = {"torch.manual_seed", "torch.cuda.manual_seed"}
    if not required.issubset(transformer.replaced):
        missing = sorted(required - transformer.replaced)
        raise AdapterError(f"Autoresearch candidate removed required seed hooks: {missing}")
    ast.fix_missing_locations(transformed)
    return ast.unparse(transformed) + "\n"


def validate_candidate_policy(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AdapterError("Autoresearch candidate train.py is not valid Python") from exc
    validator = _CandidatePolicyValidator()
    validator.visit(tree)
    if validator.evaluate_imports != 1:
        raise AdapterError("Autoresearch candidate must import evaluate_bpb exactly once from prepare")
    if validator.evaluate_calls != 1:
        raise AdapterError("Autoresearch candidate must call evaluate_bpb exactly once")


__all__ = ["SeedPolicy", "inject_seed", "validate_candidate_policy"]
