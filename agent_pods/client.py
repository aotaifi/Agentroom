"""Agent-side interface: Unix socket only, no SSH or cluster credentials."""
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import sys

MAX_MESSAGE = 24 * 1024**2


def request(payload, socket_path=None):
    raw = json.dumps(payload).encode() + b"\n"
    if len(raw) > MAX_MESSAGE:
        raise ValueError("job request is too large")
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(15)
        s.connect(socket_path or os.environ.get("AGENT_PODS_SOCKET", "/run/agent-gateway/gateway.sock"))
        s.sendall(raw)
        s.shutdown(socket.SHUT_WR)
        result = b""
        while not result.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk or len(result) + len(chunk) > MAX_MESSAGE:
                raise ValueError("invalid gateway response")
            result += chunk
    return json.loads(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("backends")
    sub.add_parser("submit", help="fails closed until trusted remote workers are installed")
    status = sub.add_parser("status")
    status.add_argument("job_id")
    p = sub.add_parser("prepare", help="create an immutable, reviewed job bundle; never execute it")
    p.add_argument("script", type=Path)
    p.add_argument("--input", type=Path, action="append", default=[])
    p.add_argument("--backend", required=True)
    for k, default in (("nodes", 1), ("cpus", 2), ("memory-mb", 2048), ("gpus", 0),
                       ("walltime-seconds", 28800), ("runtime-seconds", 18000), ("checkpoint-margin-seconds", 10800)):
        p.add_argument("--" + k, type=int, default=default)
    args = parser.parse_args()
    payload = {"action": args.action}
    if args.action == "status":
        payload["job_id"] = args.job_id
    if args.action == "prepare":
        files = {}
        for path in [args.script] + args.input:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024**2:
                parser.error("select small, regular files; symlinks are not accepted")
            if path.name in files:
                parser.error("duplicate bundle file name")
            files[path.name] = base64.b64encode(path.read_bytes()).decode()
        resource_keys = ("nodes", "cpus", "memory_mb", "gpus", "walltime_seconds", "runtime_seconds", "checkpoint_margin_seconds")
        payload.update(backend=args.backend, entrypoint=args.script.name, files=files,
                       resources={k: getattr(args, k) for k in resource_keys})
    try:
        response = request(payload)
    except (OSError, ValueError) as e:
        parser.exit(1, f"jobctl: {e}\n")
    print(json.dumps(response, indent=2))
    if not response.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
