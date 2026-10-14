"""Non-destructive checks run inside the live container."""
import errno
import argparse
import json
import os
from pathlib import Path
import socket

from .client import request


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host-home", required=True)
    p.add_argument("--host-state", required=True)
    p.add_argument("--locked-affinity", action="store_true")
    p.add_argument("--expect-bootstrap", action="store_true")
    p.add_argument("--job-mode", choices=["none", "prepare"], default="prepare")
    args = p.parse_args()
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    init_status = dict(line.split(":", 1) for line in Path("/proc/1/status").read_text().splitlines() if ":" in line)
    checks = {
        "non_root": os.getuid() == 1000,
        "no_capabilities": int(status["CapEff"].strip(), 16) == 0,
        "no_bounding_capabilities": int(status["CapBnd"].strip(), 16) == 0,
        "no_new_privileges": status["NoNewPrivs"].strip() == "1",
        "seccomp": status["Seccomp"].strip() == "2",
        "no_ssh_keys": not Path("/home/agent/.ssh").exists(),
        "no_host_home": not Path(args.host_home).exists(),
        "no_host_policy": not Path(args.host_state, "profile.json").exists(),
        "no_host_canary": not Path(args.host_state, "host-canary").exists(),
        "no_gateway_store": not Path("/gateway/jobs").exists(),
        "no_gateway_policy": not Path("/etc/agent-pods/profile.json").exists(),
        "no_docker_socket": not Path("/var/run/docker.sock").exists(),
        "no_podman_socket": not Path("/run/podman/podman.sock").exists(),
        "seed_read_only": False, "root_read_only": False,
        "gateway_mount_read_only": False, "workspace_writable": False,
        "no_outbound_route": False, "no_root_escalation": False,
    }
    for path, label in (("/etc/agent-pods-probe", "root_read_only"),
                        ("/seed/agent-pods-probe", "seed_read_only"),
                        ("/run/agent-gateway/agent-pods-probe", "gateway_mount_read_only")):
        try:
            Path(path).write_text("probe")
            Path(path).unlink()
        except OSError as e:
            checks[label] = e.errno == errno.EROFS
    workspace = Path("/workspace/.probe-write")
    workspace.write_text("sandbox")
    checks["workspace_writable"] = workspace.read_text() == "sandbox"
    workspace.unlink()
    try:
        os.setuid(0)
    except PermissionError:
        checks["no_root_escalation"] = True
    if args.locked_affinity:
        try:
            os.sched_setaffinity(0, os.sched_getaffinity(0))
            checks["affinity_change_denied"] = False
        except PermissionError:
            checks["affinity_change_denied"] = True
    with socket.socket() as s:
        s.settimeout(2)
        try:
            s.connect(("1.1.1.1", 443))
        except OSError as e:
            checks["no_outbound_route"] = e.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH)
    backend_reply = request({"action": "backends"})
    checks["gateway_connected"] = backend_reply["ok"]
    checks["gateway_mode_enforced"] = (backend_reply["mode"] == args.job_mode or
        (args.job_mode == "prepare" and backend_reply["mode"] == "prepare-only"))
    if args.expect_bootstrap:
        checks["constitution_installed"] = Path("/workspace/pod-constitution.md").is_file()
        checks["instruction_pointers_installed"] = all(
            "pod-constitution.md" in Path("/workspace", name).read_text()
            for name in ("AGENTS.md", "CLAUDE.md"))
        settings = json.loads(Path("/workspace/pod-settings.json").read_text())
        checks["settings_mode_matches"] = settings["permissions"]["jobs"] == args.job_mode
    checks["live_submission_denied"] = not request({"action": "submit"})["ok"]
    cg = Path("/sys/fs/cgroup")
    stat = os.statvfs("/workspace")
    observed = {"cpu": (cg / "cpu.max").read_text().strip() if (cg / "cpu.max").exists() else "not-delegated",
                "memory": int((cg / "memory.max").read_text()),
                "pids": int((cg / "pids.max").read_text()),
                "swap": int((cg / "memory.swap.max").read_text()),
                "affinity": sorted(os.sched_getaffinity(0)),
                "init_affinity": init_status["Cpus_allowed_list"].strip(),
                "pid_namespace": os.readlink("/proc/self/ns/pid"),
                "workspace_bytes": stat.f_blocks * stat.f_frsize}
    print(json.dumps({"checks": checks, "observed": observed, "passed": all(checks.values())}))


if __name__ == "__main__":
    main()
