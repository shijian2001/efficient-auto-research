"""Run the two failed ML-Master-2 cells with per-cell read-only monitoring."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import csv

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "BenchmarkAdapters/.venv/bin/python"
PROTOCOL = ROOT / "BenchmarkAdapters/configs/mle-protocol.n1-12h.json"
MODEL = ROOT / "BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json"
DATA = ROOT / "mle-bench-data"
CAMPAIGN = ROOT / "experiment-campaigns/20260919_mlmaster2_rerun_v4"
VARIANT = "ml-master-2@0c7b5549b5445f76864be9e88572b549bb4b3863"
# Chosen from the current low-utilization GPUs; no external process is killed.
TASKS = ("aerial-cactus-identification", "aptos2019-blindness-detection")


def free_gpu(exclude: set[str]) -> int | None:
    try:
        rows = list(csv.reader(subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
            text=True, timeout=5).splitlines()))
        apps = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid", "--format=csv,noheader"],
            text=True, timeout=5).splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    # Resolve indices with a second query; a compute app on any UUID marks its
    # card busy. Do not rely on memory alone because dormant processes retain VRAM.
    uuid_rows = list(csv.reader(subprocess.check_output(
        ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"],
        text=True, timeout=5).splitlines()))
    busy = {value.strip() for value in apps if value.strip()}
    for row, uuid_row in zip(rows, uuid_rows):
        index, memory, util = (value.strip() for value in row)
        uuid = uuid_row[1].strip()
        if index in exclude or uuid in busy:
            continue
        if int(float(memory)) <= 1024 and int(float(util)) <= 5:
            lock = ROOT / ".runtime/gpu-locks" / f"efficient-auto-research-gpu-{index}.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            probe = subprocess.run(["flock", "-n", str(lock), "-c", "true"], check=False)
            if probe.returncode == 0:
                return int(index)
    return None


def main() -> int:
    CAMPAIGN.mkdir(parents=True, exist_ok=True)
    controller = CAMPAIGN / "controller.json"
    processes = []
    monitors = []
    used = {"1", "2", "3"}  # paused MLEvolve cells remain reserved
    for task in TASKS:
        gpu = None
        while gpu is None:
            gpu = free_gpu(used)
            if gpu is None:
                print(json.dumps({"time": time.time(), "waiting_for_free_gpu": True}), flush=True)
                time.sleep(60)
        used.add(str(gpu))
        run_root = CAMPAIGN / task
        run_root.mkdir(parents=True, exist_ok=True)
        log = CAMPAIGN / f"{task}.controller.log"
        command = [str(PYTHON), "-m", "BenchmarkAdapters", "mle-cell",
                   "--protocol", str(PROTOCOL), "--agent", "ml-master-2",
                   "--agent-variant", VARIANT, "--competition-id", task,
                   "--seed", "0", "--data-root", str(DATA), "--campaign-dir", str(run_root),
                   "--gpu-id", str(gpu), "--model-config", str(MODEL)]
        stream = log.open("a", encoding="utf-8", buffering=1)
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True, env={**os.environ, "MLE_RUNTIME_ROOT": str(ROOT / ".runtime")})
        monitor_log = (CAMPAIGN / f"{task}.monitor.log").open("a", encoding="utf-8", buffering=1)
        monitor = subprocess.Popen([str(PYTHON), "-m", "BenchmarkAdapters.MLEBenchLite.runtime_monitor",
                                    "--campaign-dir", str(run_root), "--poll", "30",
                                    "--output-prefix", "host-", "--no-fsync"], cwd=ROOT,
                                   stdout=monitor_log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        processes.append((task, gpu, process, stream))
        monitors.append((task, monitor, monitor_log))
    controller.write_text(json.dumps({"started_at": time.time(), "variant": VARIANT,
        "tasks": [{"task": t, "gpu": g, "pid": p.pid} for t,g,p,_ in processes]}, indent=2)+"\n")
    print(json.dumps({"campaign": str(CAMPAIGN), "tasks": [{"task": t, "gpu": g, "pid": p.pid} for t, g, p, _ in processes]}), flush=True)
    try:
        while any(p.poll() is None for _,_,p,_ in processes):
            rows=[]
            for task,gpu,p,_ in processes: rows.append({"task":task,"gpu":gpu,"pid":p.pid,"returncode":p.poll()})
            print(json.dumps({"time":time.time(),"status":rows}), flush=True)
            time.sleep(60)
    finally:
        for _, monitor, log in monitors:
            if monitor.poll() is None:
                monitor.terminate()
            log.close()
        for _,_,p,stream in processes:
            stream.close()
    results=[]
    for task,gpu,p,_ in processes: results.append({"task":task,"gpu":gpu,"pid":p.pid,"returncode":p.returncode})
    (CAMPAIGN / "controller-final.json").write_text(json.dumps({"finished_at":time.time(),"results":results},indent=2)+"\n")
    print(json.dumps({"finished":True,"results":results}),flush=True)
    return 0 if all(row["returncode"] == 0 for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
