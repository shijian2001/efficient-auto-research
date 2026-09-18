"""Read-only supervisor for long-running ML-Master candidate processes.

The monitor deliberately does not repair, kill, or restart processes. Runtime hooks
own candidate deadlines and bounded repairs; this process records enough evidence to
spot incidents promptly and to let the campaign coordinator decide what to do.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MONITOR_DIR = Path("agent-output") / "runtime-monitor"
HEARTBEAT_GLOB = "candidate-*.json"
STALE_SECONDS = 15 * 60
LOW_DISK_BYTES = 10 * 1024**3
MAX_TAIL_BYTES = 32 * 1024
MAX_EVENT_HISTORY = 2000
SENSITIVE = re.compile(r"(?i)(?:token|secret|password|authorization|api[_-]?key)\s*[=:]\s*[^\s,;]+")
OOM_RE = re.compile(r"(?:out\s+of\s+memory|oom-kill|cuda\s+out\s+of\s+memory|cublas_status_alloc_failed)", re.I)
CUDA_LOST_RE = re.compile(r"(?:cuda(?:\s+error)?[^\n]{0,80}(?:device|context).{0,20}(?:lost|reset|unavailable)|device-side assert|nccl.*(?:unhandled|failure))", re.I)
IMPORT_RE = re.compile(r"(?:unsupported|unknown)[^\n]{0,80}(?:exec|executable)|(?:no module named|importerror|modulenotfounderror)", re.I)

REQUIRED_HEARTBEAT = (
    "pid", "pgid", "process_start_ticks", "argv", "started_at", "last_output_at",
    "deadline", "state", "stdout_path", "exit_code", "auto_actions",
)
VALID_STATES = {"running", "completed", "failed", "timed_out"}


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    path: Path
    data: dict[str, Any]


@dataclass(frozen=True)
class ProcessObservation:
    present: bool
    identity_ok: bool
    state: str | None
    pgid: int | None
    argv: tuple[str, ...]
    cpu_ticks: int | None
    gpu_active: bool
    reason: str | None = None


def _json_load(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _redact(v) for k, v in value.items() if str(k).lower() not in {"token", "secret", "password", "authorization", "api_key", "apikey"}}
    if isinstance(value, list):
        return [_redact(v) for v in value[:64]]
    if isinstance(value, str):
        value = SENSITIVE.sub(lambda m: m.group(0).split("=", 1)[0] + "=<redacted>" if "=" in m.group(0) else "<redacted>", value)
        return value[:2000]
    return value


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - MAX_TAIL_BYTES))
            return fh.read(MAX_TAIL_BYTES).decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def _validate_heartbeat(data: Any, path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(data, dict):
        return None, "heartbeat must be a JSON object"
    missing = [key for key in REQUIRED_HEARTBEAT if key not in data]
    if missing:
        return None, "missing fields: " + ", ".join(missing)
    integer_fields = ("pid", "pgid", "process_start_ticks")
    for key in integer_fields:
        if isinstance(data[key], bool) or not isinstance(data[key], int) or data[key] < 0:
            return None, f"{key} must be a non-negative integer"
    for key in ("started_at", "last_output_at", "deadline"):
        if isinstance(data[key], bool) or not isinstance(data[key], (int, float)):
            return None, f"{key} must be numeric"
    if not isinstance(data["argv"], list) or not all(isinstance(v, str) for v in data["argv"]):
        return None, "argv must be a list of strings"
    if data["state"] not in VALID_STATES:
        return None, f"state must be one of {sorted(VALID_STATES)}"
    if not isinstance(data["stdout_path"], str) or not isinstance(data["auto_actions"], list):
        return None, "stdout_path must be a string and auto_actions a list"
    if data["exit_code"] is not None and (isinstance(data["exit_code"], bool) or not isinstance(data["exit_code"], int)):
        return None, "exit_code must be an integer or null"
    return data, None


def _candidate_id(path: Path, data: dict[str, Any]) -> str:
    value = data.get("candidate_id")
    if isinstance(value, str) and value and len(value) < 200:
        return value
    return path.stem.removeprefix("candidate-")


def discover_candidates(campaign_dir: Path) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Discover only heartbeat paths; never recursively inspect arbitrary files."""
    paths = sorted(campaign_dir.glob("**/agent-output/runtime-monitor/candidate-*.json"))
    candidates: list[Candidate] = []
    errors: list[dict[str, Any]] = []
    for path in paths:
        try:
            raw = _json_load(path)
            data, error = _validate_heartbeat(raw, path)
            if error:
                errors.append({"path": str(path), "error": error})
                continue
            assert data is not None
            candidates.append(Candidate(_candidate_id(path, data), path, data))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append({"path": str(path), "error": f"unreadable heartbeat: {type(exc).__name__}"})
    return candidates, errors


def _proc_stat(pid: int) -> tuple[str, int, int, tuple[str, ...]] | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        close = stat_text.rfind(")")
        if close < 0:
            return None
        fields = stat_text[close + 2 :].split()
        # fields[0]=state, fields[2]=pgid, fields[19]=starttime (stat field 22).
        state, pgid, start_ticks = fields[0], int(fields[2]), int(fields[19])
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        argv = tuple(x.decode("utf-8", "replace") for x in raw.split(b"\0") if x)
        return state, pgid, start_ticks, argv
    except (OSError, ValueError, IndexError):
        return None


def _gpu_pids() -> set[int]:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    result: set[int] = set()
    for line in proc.stdout.splitlines():
        try:
            result.add(int(line.strip()))
        except ValueError:
            continue
    return result


def observe_process(data: dict[str, Any], gpu_pids: set[int] | None = None) -> ProcessObservation:
    pid = int(data["pid"])
    info = _proc_stat(pid)
    if info is None:
        return ProcessObservation(False, False, None, None, (), None, False, "process absent")
    state, pgid, start_ticks, argv = info
    identity_ok = start_ticks == int(data["process_start_ticks"]) and pgid == int(data["pgid"])
    if not identity_ok:
        return ProcessObservation(True, False, state, pgid, argv, None, False, "pid identity mismatch")
    gpu_active = pid in (gpu_pids if gpu_pids is not None else set())
    cpu_ticks = None
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        close = stat_text.rfind(")")
        cpu_fields = stat_text[close + 2 :].split()
        cpu_ticks = int(cpu_fields[11]) + int(cpu_fields[12])
    except (OSError, ValueError, IndexError):
        pass
    return ProcessObservation(True, True, state, pgid, argv, cpu_ticks, gpu_active)


def _result_state(campaign_dir: Path, candidate_path: Path | None = None) -> str | None:
    paths = [campaign_dir / "result.json"]
    if candidate_path is not None:
        # .../<cell>/agent-output/runtime-monitor/candidate-*.json -> <cell>/result.json
        try:
            paths.append(candidate_path.parent.parent.parent / "result.json")
        except IndexError:
            pass
    for path in paths:
        try:
            raw = _json_load(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and isinstance(raw.get("status"), str):
            return raw["status"]
    return None


def _event_key(event: dict[str, Any]) -> str:
    return json.dumps({k: event.get(k) for k in ("candidate_id", "code", "severity", "detail")}, sort_keys=True, ensure_ascii=False)


def _existing_event_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    try:
        with path.open(encoding="utf-8") as fh:
            lines = fh.readlines()[-MAX_EVENT_HISTORY:]
    except OSError:
        return keys
    for line in lines:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                keys.add(_event_key(item))
        except (ValueError, TypeError):
            continue
    return keys


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(_redact(row), ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_redact(value), fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class RuntimeMonitor:
    def __init__(self, campaign_dir: Path, *, now: callable = time.time) -> None:
        self.campaign_dir = campaign_dir.resolve()
        self.now = now
        self.status_path = self.campaign_dir / "agent-output" / "runtime-monitor" / "status.json"
        self.events_path = self.campaign_dir / "agent-output" / "runtime-monitor" / "events.jsonl"
        self.incidents_path = self.campaign_dir / "agent-output" / "runtime-monitor" / "incidents.jsonl"
        self._previous_cpu: dict[str, int] = {}
        self._oom_seen: dict[str, int] = {}

    def poll(self) -> dict[str, Any]:
        now = float(self.now())
        candidates, malformed = discover_candidates(self.campaign_dir)
        gpu_pids = _gpu_pids()
        observations: dict[str, ProcessObservation] = {}
        incidents: list[dict[str, Any]] = []
        all_error_text: list[tuple[str, str]] = []
        for item in candidates:
            data = item.data
            obs = observe_process(data, gpu_pids)
            observations[item.candidate_id] = obs
            text = str(data.get("failure_reason", ""))
            stdout = Path(data["stdout_path"])
            if not stdout.is_absolute():
                stdout = self.campaign_dir / stdout
            text += "\n" + _tail(stdout)
            all_error_text.append((item.candidate_id, text))
            if not obs.identity_ok and obs.present:
                incidents.append(self._incident(item.candidate_id, "identity_mismatch", "major", obs.reason or "pid identity mismatch"))
            if data["state"] == "running" and not obs.present:
                severity = "major" if now >= float(data["deadline"]) else "warning"
                incidents.append(self._incident(item.candidate_id, "process_absent", severity, "running heartbeat has no matching process"))
            elif data["state"] == "running" and now >= float(data["deadline"]):
                incidents.append(self._incident(item.candidate_id, "deadline_exceeded", "major", "candidate deadline has passed while process remains present"))
            if data["state"] == "running" and now - float(data["last_output_at"]) > STALE_SECONDS:
                old_cpu = self._previous_cpu.get(item.candidate_id)
                cpu_active = obs.cpu_ticks is not None and old_cpu is not None and obs.cpu_ticks > old_cpu
                if obs.identity_ok and not cpu_active and not obs.gpu_active:
                    incidents.append(self._incident(item.candidate_id, "no_activity", "major", f"no output or CPU/GPU activity for {STALE_SECONDS}s"))
                elif obs.identity_ok:
                    incidents.append(self._incident(item.candidate_id, "stale_but_active", "info", "no output recently, but process activity is present"))
            if obs.cpu_ticks is not None:
                self._previous_cpu[item.candidate_id] = obs.cpu_ticks
        for item in malformed:
            incidents.append(self._incident(None, "malformed_heartbeat", "major", item["error"], path=item["path"]))
        oom = [(cid, txt) for cid, txt in all_error_text if OOM_RE.search(txt)]
        for cid, _ in oom:
            self._oom_seen[cid] = self._oom_seen.get(cid, 0) + 1
        repeated_oom_ids = {cid for cid, count in self._oom_seen.items() if count >= 2}
        cuda = [(cid, txt) for cid, txt in all_error_text if CUDA_LOST_RE.search(txt)]
        imports = [(cid, txt) for cid, txt in all_error_text if IMPORT_RE.search(txt)]
        if len(oom) >= 2 or repeated_oom_ids:
            incidents.append(self._incident(None, "repeated_oom", "major", f"OOM evidence in {len(oom)} candidates; repeated candidates: {sorted(repeated_oom_ids)}"))
        elif oom:
            incidents.append(self._incident(oom[0][0], "oom", "warning", "OOM evidence found in candidate output"))
        if cuda:
            incidents.append(self._incident(cuda[0][0], "cuda_failure", "major", "CUDA device/context failure evidence found"))
        if imports:
            incidents.append(self._incident(imports[0][0], "unsupported_exec_import", "major", "unsupported executable/import failure evidence found"))
        disk = shutil.disk_usage(self.campaign_dir)
        if disk.free < LOW_DISK_BYTES:
            incidents.append(self._incident(None, "low_disk", "major", f"free bytes {disk.free} below {LOW_DISK_BYTES}"))
        event_keys = _existing_event_keys(self.events_path)
        emitted = []
        for incident in incidents:
            incident["observed_at"] = now
            incident["event_key"] = _event_key(incident)
            if incident["event_key"] not in event_keys:
                emitted.append(incident)
                event_keys.add(incident["event_key"])
        _append_jsonl(self.events_path, emitted)
        _append_jsonl(self.incidents_path, emitted)
        candidate_rows = []
        for item in candidates:
            obs = observations[item.candidate_id]
            candidate_rows.append({
                "candidate_id": item.candidate_id, "path": str(item.path), "state": item.data["state"],
                "pid": item.data["pid"], "identity_ok": obs.identity_ok, "process_present": obs.present,
                "process_state": obs.state, "cpu_ticks": obs.cpu_ticks, "gpu_active": obs.gpu_active,
                "last_output_at": item.data["last_output_at"], "deadline": item.data["deadline"],
                "exit_code": item.data["exit_code"], "auto_actions": _redact(item.data["auto_actions"]),
                "result_state": _result_state(self.campaign_dir, item.path),
            })
        status = {
            "schema_version": 1, "observed_at": now, "campaign_dir": str(self.campaign_dir),
            "disk": {"free_bytes": disk.free, "used_bytes": disk.used, "total_bytes": disk.total, "low_threshold_bytes": LOW_DISK_BYTES},
            "candidates": candidate_rows,
            "summary": {"total": len(candidates), "malformed": len(malformed), "running": sum(x["state"] == "running" for x in candidate_rows), "completed": sum(x["state"] == "completed" for x in candidate_rows), "failed": sum(x["state"] in {"failed", "timed_out"} for x in candidate_rows), "major_incidents": sum(x["severity"] == "major" for x in incidents)},
            "incidents": incidents[-100:], "monitor": {"stale_seconds": STALE_SECONDS, "read_only": True},
        }
        _atomic_json(self.status_path, status)
        return status

    @staticmethod
    def _incident(candidate_id: str | None, code: str, severity: str, detail: str, **extra: Any) -> dict[str, Any]:
        item = {"candidate_id": candidate_id, "code": code, "severity": severity, "detail": detail}
        item.update(extra)
        return _redact(item)

    def run_forever(self, poll_seconds: float = 30.0) -> None:
        while True:
            self.poll()
            time.sleep(max(0.1, poll_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--poll", type=float, default=30.0, help="poll interval in seconds (default: 30)")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    args = parser.parse_args(argv)
    if not args.campaign_dir.is_dir():
        parser.error(f"campaign directory does not exist: {args.campaign_dir}")
    monitor = RuntimeMonitor(args.campaign_dir)
    if args.once:
        monitor.poll()
    else:
        monitor.run_forever(args.poll)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
