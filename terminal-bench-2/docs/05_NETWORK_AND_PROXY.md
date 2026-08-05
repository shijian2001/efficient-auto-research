# Network and Proxy

## Local Proxy

The host runs `mihomo` on `127.0.0.1:17892`. It supports both HTTP CONNECT and
SOCKS5 traffic.

The installation shell currently exports both uppercase and lowercase HTTP and
HTTPS proxy variables pointing to this port. Direct connectivity tests through
the proxy returned HTTP 200 from GitHub and PyPI.

## Registry Download Failure

The first command using the new dataset identifier
`terminal-bench/terminal-bench-2` reached the registry and discovered all 89
tasks, but failed at `0/89` with an HTTP/2 state error. This confirms that name
resolution, registry access, and proxy routing worked.

The installation therefore used the official compatibility identifier
`terminal-bench@2.0`, which completed all `89/89` tasks.

## Container Access

Docker containers cannot reach a service bound only to host loopback. The run
scripts create a temporary listener on `172.17.0.1:17893` and forward it to
`127.0.0.1:17892`. Proxy variables injected into Agent and verifier phases use
the Docker bridge address. The temporary listener is stopped on command exit.

Raw audit: `logs/08_proxy_audit.log`
Failed registry command: `logs/07_dataset_download.log`
