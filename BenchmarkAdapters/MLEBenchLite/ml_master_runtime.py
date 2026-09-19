"""ML-Master execution safeguards; native Agent retains solution decisions."""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

MAX_RETURN_BYTES = 512 * 1024


def _apply_candidate_runtime_guards(cwd: str | None, environment: dict[str, str], actions: list[str]) -> None:
    """Apply deterministic guards to generated training scripts when requested.

    APTOS images are large enough that CUDA-initialized forked DataLoader workers
    can stall before the first batch on a busy host.  The guard is opt-in from
    the adapter and only changes the generated script's worker count; it does
    not alter model code or data.
    """
    if environment.get("ML_MASTER_FORCE_NUM_WORKERS") != "0" or not cwd:
        return
    path = Path(cwd) / "run.py"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return
    updated, count = re.subn(
        r"(?m)^(\s*NUM_WORKERS\s*=\s*).*$",
        r"\g<1>0",
        source,
        count=1,
    )
    if count and updated != source:
        path.write_text(updated, encoding="utf-8")
        actions.append("force_zero_dataloader_workers_for_stable_ipc")


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".heartbeat-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def process_start_ticks(pid: int) -> int:
    data = Path(f"/proc/{pid}/stat").read_text()
    return int(data[data.rfind(")") + 2:].split()[19])


def tail(path: Path, maximum: int = MAX_RETURN_BYTES) -> str:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - maximum))
        text = stream.read(maximum).decode("utf-8", "replace")
    return (f"[Earlier output retained in {path}]\n" if size > maximum else "") + text


def stop_group(process: subprocess.Popen, grace: float = 3) -> None:
    # Groups are created by this helper. Never signal an arbitrary recorded PID.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    end = time.monotonic() + grace
    while time.monotonic() < end:
        if process.poll() is not None:
            break
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_candidate(command: str, cwd: str | None, environment: dict[str, str], timeout: float) -> dict:
    """Persist child output and return bounded failures to the native debug loop."""
    root = Path(environment["ML_MASTER_MONITOR_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    stdout_path = root / f"candidate-{ident}.stdout.log"
    stderr_path = root / f"candidate-{ident}.stderr.log"
    heartbeat_path = root / f"candidate-{ident}.json"
    env = dict(environment)
    env["PYTHONUNBUFFERED"] = "1"
    actions = []
    short_tmp = env.get("ML_MASTER_SHORT_TMPDIR", "/tmp")
    if env.get("TMPDIR") != short_tmp:
        actions.append("reset_tmpdir_to_short_sandbox_path")
    env["TMPDIR"] = short_tmp
    if env.get("CUDA_VISIBLE_DEVICES") != "0":
        actions.append("reset_gpu_to_single_sandbox_ordinal")
    env["CUDA_VISIBLE_DEVICES"] = "0"
    _apply_candidate_runtime_guards(cwd, env, actions)
    started = time.time()
    global_deadline = float(env.get("ML_MASTER_DEADLINE_EPOCH", started + timeout + 90))
    allowance = min(float(timeout), float(env.get("ML_MASTER_CHILD_TIMEOUT_SECONDS", "5400")),
                    max(0, global_deadline - started - 90))
    if allowance <= 0:
        message = "Global run budget exhausted; preserve the existing best submission."
        return {"stdout":"", "stderr":message, "exit_code":-1,"output":message}
    deadline = started + allowance
    mono_deadline = time.monotonic() + allowance
    state = "failed"
    failure = None
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        process = subprocess.Popen(command, shell=True, cwd=cwd, env=env,
                                   stdout=out, stderr=err, start_new_session=True)
        data = {"schema_version":1, "candidate_id":ident, "pid":process.pid,
                "pgid":process.pid, "process_start_ticks":process_start_ticks(process.pid),
                "pid_namespace":os.readlink("/proc/self/ns/pid"),
                "argv":["/bin/sh", "-c", command], "cwd":str(cwd),
                "started_at":started, "last_output_at":started, "deadline":deadline,
                "state":"running", "stdout_path":str(stdout_path), "stderr_path":str(stderr_path),
                "task_id":env.get("ML_MASTER_TASK_ID"), "agent_id":"ml-master-2",
                "exit_code":None, "auto_actions":actions}
        prev_sizes = (-1,-1)
        try:
            while process.poll() is None:
                now = time.time()
                sizes = (stdout_path.stat().st_size, stderr_path.stat().st_size)
                if sizes != prev_sizes:
                    data["last_output_at"] = now
                    prev_sizes = sizes
                data["updated_at"] = now
                atomic_json(heartbeat_path, data)
                errors = tail(stderr_path, 16384)
                if "AF_UNIX path too long" in errors or "No space left on device" in errors:
                    failure = "Infrastructure error in candidate stderr; stopped candidate for native recovery."
                    actions.append("stop_known_infrastructure_failure_and_return_to_debug")
                    stop_group(process)
                    break
                if time.monotonic() >= mono_deadline:
                    state = "timed_out"
                    failure = (f"Command timed out after {allowance:.0f}s. Remaining whole-run budget "
                               f"{max(0,global_deadline-now):.0f}s. Produce a valid submission within that budget; "
                               "all earlier best artifacts remain intact.")
                    actions.append("stop_candidate_at_deadline_and_return_to_debug")
                    stop_group(process)
                    break
                time.sleep(min(2, max(0.01, mono_deadline-time.monotonic())))
            if failure is None:
                state = "completed" if process.returncode == 0 else "failed"
                if state == "failed":
                    actions.append("return_error_to_native_debug_agent")
        finally:
            stop_group(process, grace=0.2)
            data.update(state=state, exit_code=-1 if failure else process.returncode,
                        updated_at=time.time(), failure_reason=failure, auto_actions=actions)
            atomic_json(heartbeat_path, data)
    stdout, stderr = tail(stdout_path), tail(stderr_path)
    if failure:
        stderr += "\n" + failure
    return {"stdout":stdout,"stderr":stderr,"exit_code":data["exit_code"],"output":stdout+stderr}


def preflight() -> dict:
    """Prove CUDA and multiprocessing IPC before spending model requests."""
    import torch
    from torch.utils.data import DataLoader, TensorDataset
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("CUDA unavailable or non-exclusive inside ML-Master sandbox")
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    loader = DataLoader(TensorDataset(torch.zeros(8,3,8,8)), batch_size=4, num_workers=2, timeout=30)
    iterator = iter(loader)
    try:
        batch = next(iterator)[0]
        device_batch = batch.cuda()
        torch.cuda.synchronize()
        result = {"cuda_available":True,"gpu_name":torch.cuda.get_device_name(0),
                  "device_count":1,"ipc_batch_shape":list(batch.shape),"tmpdir":tempfile.gettempdir()}
        del device_batch
    finally:
        iterator._shutdown_workers()
        torch.set_num_threads(previous_threads)
        torch.cuda.empty_cache()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-seconds", type=float, required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--task-id",required=True)
    parser.add_argument("child",nargs=argparse.REMAINDER)
    args=parser.parse_args(argv)
    child=args.child[1:] if args.child[:1]==["--"] else args.child
    if not child:
        parser.error("child command required")
    output=args.output_dir.resolve()
    directory=output/"runtime-monitor"
    directory.mkdir(parents=True,exist_ok=True)
    started=time.time()
    deadline=started+args.budget_seconds
    os.environ.update(TMPDIR="/tmp",PYTHONUNBUFFERED="1",CUDA_VISIBLE_DEVICES="0",
                      ML_MASTER_DEADLINE_EPOCH=str(deadline))
    try:
        readiness=preflight()
    except Exception as exc:
        atomic_json(directory/"preflight.json",{"ok":False,"error":str(exc),"time":time.time()})
        raise
    atomic_json(directory/"preflight.json",{"ok":True,"time":time.time(),**readiness})
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    from BenchmarkAdapters.MLEBenchLite.runtime_monitor import RuntimeMonitor
    # The wrapper is inside bwrap.  Its mount namespace can report a private
    # filesystem view, so disk exhaustion must be checked by the host-side
    # monitor; the in-sandbox monitor still records candidate/process events.
    monitor=RuntimeMonitor(
        output.parent,
        disk_scope="sandbox",
        output_prefix="internal-",
        durable_writes=False,
    )
    os.environ["ML_MASTER_RUN_TIMEOUT_SECONDS"]=str(max(1,int(deadline-time.time())))
    with (directory/"native-output.log").open("wb") as stream:
        process=subprocess.Popen(child,env=os.environ.copy(),stdout=stream,stderr=subprocess.STDOUT,
                                 start_new_session=True)
        def terminate(signum,frame):
            stop_group(process)
            raise SystemExit(128+signum)
        signal.signal(signal.SIGTERM,terminate)
        signal.signal(signal.SIGINT,terminate)
        heartbeat={"pid":process.pid,"process_start_ticks":process_start_ticks(process.pid),
                   "pid_namespace":os.readlink("/proc/self/ns/pid"),"started_at":started,
                   "deadline":deadline,"task_id":args.task_id,"state":"running"}
        try:
            while process.poll() is None:
                heartbeat["updated_at"]=time.time()
                atomic_json(directory/"native-heartbeat.json",heartbeat)
                monitor.poll()
                if time.time()>=deadline:
                    stop_group(process)
                    heartbeat["state"]="budget_expired"
                    break
                time.sleep(5)
        finally:
            stop_group(process)
            heartbeat.update(updated_at=time.time(),return_code=process.returncode)
            if heartbeat["state"]=="running":
                heartbeat["state"]="completed" if process.returncode==0 else "failed"
            atomic_json(directory/"native-heartbeat.json",heartbeat)
            monitor.poll()
    return 0 if heartbeat["state"]=="budget_expired" else int(process.returncode or 0)


if __name__=="__main__":
    raise SystemExit(main())
