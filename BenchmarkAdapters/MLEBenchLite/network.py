"""Network policy for MLE runs, including isolated Agents and image downloads."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

from ..contracts import AdapterError
from ..registry import ROOT
from ..tcp_forwarder import _Server


MLE_PROXY = "http://127.0.0.1:17892"


def proxy_environment(proxy: str = MLE_PROXY) -> dict[str, str]:
    host = urlsplit(proxy).hostname or ""
    bypass = ",".join(dict.fromkeys(("localhost", "127.0.0.1", "::1", host)))
    return {
        **{name: proxy for name in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
        )},
        "NO_PROXY": bypass,
        "no_proxy": bypass,
    }


def upstream_proxy(base_url: str) -> str:
    # Local relay traffic stays local; only the external API hop uses Clash.
    return "" if urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"} else MLE_PROXY


def check_proxy() -> None:
    try:
        with socket.create_connection(("127.0.0.1", 17892), timeout=5):
            pass
    except OSError as exc:
        raise AdapterError(f"required MLE proxy is unavailable: {MLE_PROXY}") from exc


@contextmanager
def agent_download_proxy():
    """Expose only Clash on an address reachable from bwrap and Docker bridges."""
    check_proxy()
    gateway = subprocess.check_output(
        ["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
        text=True, timeout=30,
    ).strip()
    if not gateway or gateway.startswith("127."):
        raise AdapterError("MLE download proxy requires a Docker bridge gateway")
    with _Server((gateway, 0), ("127.0.0.1", 17892)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://{gateway}:{server.server_address[1]}"
        finally:
            server.shutdown()
            thread.join()


def ensure_image(image: str) -> None:
    """Pull with crane through Clash, then load locally without a daemon restart."""
    def exists() -> bool:
        return subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        ).returncode == 0

    cache = ROOT / "cache" / "proxy-images"
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = cache / (hashlib.sha256(image.encode()).hexdigest() + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if exists():
            return
        check_proxy()
        crane = ROOT / "cache" / "tools" / "crane"
        if not crane.is_file():
            raise AdapterError("install the MLE image downloader: bash docker-eval/install_crane.sh")
        environment = {**os.environ, **proxy_environment()}
        with tempfile.TemporaryDirectory(prefix="image-", dir=cache) as directory:
            archive = Path(directory) / "image.tar"
            subprocess.run(
                [str(crane), "pull", "--platform", "linux/amd64", image, str(archive)],
                env=environment, check=True, timeout=7200,
            )
            subprocess.run(["docker", "load", "--input", str(archive)], check=True, timeout=1800)
        if not exists():
            raise AdapterError(f"proxy image download did not provide {image}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?")
    args = parser.parse_args()
    check_proxy()
    if args.image:
        ensure_image(args.image)


if __name__ == "__main__":
    main()
