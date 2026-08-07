"""Run Bubblewrap with slirp4netns-backed outbound networking."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a Bubblewrap command after --")
    if shutil.which("slirp4netns") is None:
        raise RuntimeError("slirp4netns is required for isolated Agent networking")

    info_read, info_write = os.pipe()
    gate_read, gate_write = os.pipe()
    ready_read, ready_write = os.pipe()
    exit_read, exit_write = os.pipe()
    try:
        try:
            insert_at = command.index("--chdir")
        except ValueError as exc:
            raise RuntimeError("Bubblewrap command is missing --chdir") from exc
        bwrap_command = [
            *command[:insert_at],
            "--info-fd",
            str(info_write),
            "--block-fd",
            str(gate_read),
            *command[insert_at:],
        ]
        bwrap = subprocess.Popen(
            bwrap_command,
            pass_fds=(info_write, gate_read),
            close_fds=True,
        )
        os.close(info_write)
        info_write = -1
        with os.fdopen(info_read, "r", encoding="utf-8") as handle:
            info = json.load(handle)
        info_read = -1
        child_pid = int(info["child-pid"])
        slirp = subprocess.Popen(
            [
                "slirp4netns",
                "--configure",
                "--disable-host-loopback",
                "--ready-fd",
                str(ready_write),
                "--exit-fd",
                str(exit_read),
                str(child_pid),
                "tap0",
            ],
            pass_fds=(ready_write, exit_read),
            close_fds=True,
        )
        os.close(ready_write)
        ready_write = -1
        os.close(exit_read)
        exit_read = -1
        if not os.read(ready_read, 1):
            bwrap.terminate()
            raise RuntimeError("slirp4netns did not become ready")
        os.write(gate_write, b"1")
        os.close(gate_write)
        gate_write = -1
        return_code = bwrap.wait()
        os.close(exit_write)
        exit_write = -1
        slirp.wait(timeout=10)
        return return_code
    finally:
        for descriptor in (
            info_read,
            info_write,
            gate_read,
            gate_write,
            ready_read,
            ready_write,
            exit_read,
            exit_write,
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
