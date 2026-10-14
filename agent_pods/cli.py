"""Host operator CLI. Agent processes use exec; they never receive this host tool."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .policy import size_bytes, validate_profile
from .bootstrap import install_constitution

ROOT = Path(__file__).resolve().parents[1]


def run(*args, capture=False, **kw):
    return subprocess.run(list(args), check=True, text=True, capture_output=capture, **kw)


def state_root():
    local = Path("/scratch-local") / os.environ.get("USER", "agent")
    default = local / "agent-pods-state" if local.is_dir() else Path.home() / ".local/state/agent-pods"
    return Path(os.environ.get("AGENT_PODS_STATE", default)).resolve()


def profile_at(path):
    return validate_profile(json.loads(Path(path).read_text()))


def cpu_plan(count, runtime=None):
    """Use quotas when delegated; otherwise lock whole-CPU affinity with seccomp."""
    controllers = Path(f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/cgroup.controllers")
    if controllers.exists() and "cpu" in controllers.read_text().split():
        return {"mode": "quota", "cpus": count}
    available = sorted(os.sched_getaffinity(0))
    if int(count) != count or count > len(available):
        raise ValueError("CPU quota is not delegated: choose a whole CPU count within the host affinity")
    plan = {"mode": "locked-affinity", "cpus": available[:int(count)]}
    if runtime is not None:
        default = Path("/usr/share/containers/seccomp.json")
        if not default.is_file():
            raise ValueError("default Podman seccomp profile is required for locked affinity")
        seccomp = json.loads(default.read_text())
        for rule in seccomp["syscalls"]:
            rule["names"] = [n for n in rule["names"] if n != "sched_setaffinity"]
        seccomp["syscalls"] = [s for s in seccomp["syscalls"] if s["names"]]
        seccomp["syscalls"].append({"names": ["sched_setaffinity"], "action": "SCMP_ACT_ERRNO", "errnoRet": 1})
        path = runtime / "seccomp.json"
        path.write_text(json.dumps(seccomp))
        plan["seccomp"] = str(path)
        shim = runtime / "agentroom-crun"
        shutil.copyfile(ROOT / "agent_pods/affinity_runtime.py", shim)
        shim.chmod(0o700)
        plan["oci_runtime"] = str(shim)
    return plan


def pin_command(args, plan):
    if plan["mode"] == "locked-affinity":
        if args[0] == "podman" and plan.get("oci_runtime"):
            args = ["podman", "--runtime", plan["oci_runtime"], *args[1:]]
        return ["taskset", "--cpu-list", ",".join(map(str, plan["cpus"])), *args]
    return args


def runtime_at(name):
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        raise ValueError("invalid sandbox name")
    return state_root() / name


def secure_copy(source, dest, max_bytes):
    """Snapshot regular files; no symlinks, secrets, home mounts or live source writes."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError("source must be a real directory")
    total = 0
    excluded = {".git", ".chatty", ".ssh", ".aws", ".config", ".runtime", ".private", "__pycache__", "node_modules", "jobs", "security-test"}
    for parent, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if d not in excluded and not d.startswith(".venv")]
        for d in dirs:
            if (Path(parent) / d).is_symlink():
                raise ValueError("source symlinks are not accepted")
        for name in files:
            if name.startswith(".env") or name.endswith((".pem", ".key", ".pyc")):
                continue
            path = Path(parent) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("source must contain only regular files")
            total += path.stat().st_size
            if total > max_bytes:
                raise ValueError("source exceeds half the configured workspace capacity")
            target = dest / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            target.chmod(0o644)
    return total


def container_args(p, runtime, plan=None):
    r = p["resources"]
    plan = plan or {"mode": "quota"}
    args = ["podman", "run", "--detach", "--init", "--pull=never", "--pod", p["name"],
            "--name", p["name"] + "-agent", "--label", "io.agent-pods.managed=1",
            "--user", "1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--read-only", "--read-only-tmpfs=false", "--http-proxy=false",
            "--memory", r["memory"], "--memory-swap", r["memory"],
            "--pids-limit", str(r["pids"]), "--shm-size=32m", "--ulimit=nofile=1024:1024",
            "--volume", str(runtime / "seed") + ":/seed:ro,Z",
            "--volume", str(runtime / "socket") + ":/run/agent-gateway:ro,Z"]
    if plan["mode"] == "quota":
        args.extend(["--cpus", str(r["cpus"])])
    else:
        args.extend(["--security-opt", "seccomp=" + plan["seccomp"]])
    for path, size in (("/workspace", r["workspace"]), ("/home/agent", r["home"]),
                       ("/tmp", r["tmp"]), ("/run", "16m")):
        args.extend(["--tmpfs", f"{path}:rw,nosuid,nodev,size={size},mode=1777"])
    # /workspace and /home permit local execution; /tmp and /run are noexec.
    for i, arg in enumerate(args):
        if arg.startswith(("/tmp:rw,", "/run:rw,")):
            args[i] = arg.replace(":rw,", ":rw,noexec,")
    for key, value in p["environment"].items():
        args.extend(["--env", key + "=" + value])
    args.extend(["--env", "AGENT_PODS_SOCKET=/run/agent-gateway/gateway.sock", p["image"],
                 "/bin/bash", "-c", "cp -R /seed/. /workspace/ && touch /workspace/.agent-pods-ready && exec sleep infinity"])
    return pin_command(args, plan)


def launch_profile(args):
    p = profile_at(args.profile)
    if args.name is not None:
        p["name"] = args.name
    for field in ("cpus", "memory", "workspace", "pids"):
        value = getattr(args, field)
        if value is not None:
            p["resources"][field] = value
    if args.jobs is not None:
        p["permissions"]["jobs"] = args.jobs
    return validate_profile(p)


def start(args):
    p = launch_profile(args)
    runtime = runtime_at(p["name"])
    if runtime.exists():
        raise ValueError(f"sandbox state already exists at {runtime}; inspect or stop it first")
    info = json.loads(run("podman", "info", "--format=json", capture=True).stdout)
    if not info["host"]["security"]["rootless"] or info["host"].get("cgroupVersion") != "v2":
        raise ValueError("rootless Podman and delegated cgroup v2 are required; limits will not be relaxed")
    run("podman", "image", "exists", p["image"])
    runtime.mkdir(mode=0o700, parents=True)
    (runtime / "seed").mkdir(mode=0o700)
    (runtime / "socket").mkdir(mode=0o700)
    (runtime / "profile.json").write_text(json.dumps(p, indent=2) + "\n")
    pod_created = False
    try:
        plan = cpu_plan(p["resources"]["cpus"], runtime)
        (runtime / "limits.json").write_text(json.dumps(plan, indent=2) + "\n")
        capacity = size_bytes(p["resources"]["workspace"]) // 2
        source_bytes = secure_copy(Path(args.source).resolve(), runtime / "seed", capacity)
        source_bytes += install_constitution(runtime / "seed", ROOT / "pod-constitution.md", p, plan)
        if source_bytes > capacity:
            raise ValueError("source plus startup instructions exceeds half the workspace capacity")
        r = p["resources"]
        pod_args = ["podman", "pod", "create", "--name", p["name"], "--label", "io.agent-pods.managed=1",
            "--network=none", "--share=net,uts", "--userns=keep-id:uid=1000,gid=1000",
            "--memory", r["memory"], "--memory-swap", r["memory"]]
        if plan["mode"] == "quota":
            pod_args.extend(["--cpus", str(r["cpus"])])
        run(*pin_command(pod_args, plan))
        pod_created = True
        (runtime / "jobs").mkdir(mode=0o700)
        gateway_args = ["podman", "run", "--detach", "--init", "--pull=never", "--pod", p["name"],
                        "--name", p["name"] + "-gateway", "--label", "io.agent-pods.managed=1",
                        "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                        "--read-only", "--read-only-tmpfs=false", "--http-proxy=false",
                        "--memory=128m", "--memory-swap=128m", "--pids-limit=32",
                        "--tmpfs=/gateway:rw,nosuid,nodev,noexec,size=16m,mode=1777",
                        "--volume", str(runtime / "socket") + ":/gateway/socket:rw,Z",
                        "--volume", str(runtime / "jobs") + ":/gateway/jobs:rw,Z",
                        "--volume", str(runtime / "profile.json") + ":/etc/agent-pods/profile.json:ro,Z"]
        if plan["mode"] == "quota":
            gateway_args.extend(["--cpus", str(r["cpus"])])
        else:
            gateway_args.extend(["--security-opt", "seccomp=" + plan["seccomp"]])
        gateway_args.extend(["localhost/agent-pods-base:0.1", "python", "-m", "agent_pods.gateway",
                             "--profile", "/etc/agent-pods/profile.json", "--runtime", "/gateway"])
        run(*pin_command(gateway_args, plan))
        for _ in range(100):
            if (runtime / "socket/gateway.sock").exists():
                break
            time.sleep(0.05)
        else:
            raise ValueError("gateway was not ready; inspect podman logs " + p["name"] + "-gateway")
        run(*container_args(p, runtime, plan))
        for _ in range(60):
            ready = subprocess.run(pin_command(["podman", "exec", p["name"] + "-agent", "test", "-f", "/workspace/.agent-pods-ready"], plan),
                                   capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise ValueError("workspace initialization failed")
        if plan["mode"] == "locked-affinity":
            # Both the original process and later execs must have the same mask.
            check = "import os; assert sorted(os.sched_getaffinity(0)) == " + repr(plan["cpus"]) + "; assert sorted(os.sched_getaffinity(1)) == " + repr(plan["cpus"])
            run(*pin_command(["podman", "exec", p["name"] + "-agent", "python", "-c", check], plan))
        print(json.dumps({"name": p["name"], "state": "running", "source_bytes": source_bytes,
                          "resources": r, "network": "none", "permissions": p["permissions"],
                          "gateway": p["permissions"]["jobs"], "instructions": "pod-constitution.md",
                          "cpu_enforcement": plan, "runtime": str(runtime)}, indent=2))
    except BaseException:
        if pod_created:
            subprocess.run(["podman", "pod", "rm", "--force", p["name"]], capture_output=True)
        shutil.rmtree(runtime)
        raise


def check_managed(name):
    runtime = runtime_at(name)
    p = profile_at(runtime / "profile.json")
    inspect = json.loads(run("podman", "inspect", name + "-agent", capture=True).stdout)[0]
    if inspect["Config"].get("Labels", {}).get("io.agent-pods.managed") != "1":
        raise ValueError("refusing to operate on an unmanaged container")
    return runtime, p, inspect


def export_workspace(name):
    runtime, _, _ = check_managed(name)
    out = state_root() / "exports"
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = out / f"{name}-{time.time_ns()}.tar"
    # Never unpack agent-controlled filenames or symlinks on the host.
    with path.open("xb") as f:
        subprocess.run(["podman", "cp", name + "-agent:/workspace/.", "-"], stdout=f, check=True)
    path.chmod(0o600)
    print(json.dumps({"archive": str(path), "bytes": path.stat().st_size,
                      "note": "agent-controlled archive; review entries before extraction"}))
    return path


def stop(name):
    runtime, _, _ = check_managed(name)
    export_workspace(name)
    run("podman", "pod", "rm", "--force", name)
    # Keep the host-owned job bundles for review; allow another start with this name.
    archived = runtime.with_name(name + "-stopped-" + str(time.time_ns()))
    runtime.rename(archived)
    print(json.dumps({"state": "stopped", "retained_state": str(archived)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("kind", nargs="?", default="base", choices=["base"])
    launch = sub.add_parser("start")
    launch.add_argument("--profile", required=True)
    launch.add_argument("--source", required=True)
    launch.add_argument("--name", help="override the profile's pod name")
    launch.add_argument("--cpus", type=float, help="override the CPU limit")
    launch.add_argument("--memory", help="override RAM, for example 4g")
    launch.add_argument("--workspace", help="override workspace capacity, for example 768m")
    launch.add_argument("--pids", type=int, help="override process limit")
    launch.add_argument("--jobs", choices=["none", "prepare", "submit"],
                        help="job permission; submit requires an adapter and is rejected in this release")
    for op in ("status", "export", "stop", "verify"):
        sub.add_parser(op).add_argument("name")
    execute = sub.add_parser("exec")
    execute.add_argument("name")
    execute.add_argument("argv", nargs=argparse.REMAINDER)
    a = parser.parse_args()
    try:
        if a.command == "build":
            plan = cpu_plan(2)
            command = ["podman", "build", "--layers", "--memory=2g",
                "-f", str(ROOT / "images" / a.kind / "Containerfile"),
                "-t", "localhost/agent-pods-" + a.kind + ":0.1", str(ROOT)]
            if plan["mode"] == "quota":
                command[3:3] = ["--cpu-quota=200000", "--cpu-period=100000"]
            run(*pin_command(command, plan))
        elif a.command == "start":
            start(a)
        elif a.command == "status":
            runtime, p, i = check_managed(a.name)
            print(json.dumps({"name": a.name, "running": i["State"]["Running"], "container_id": i["Id"],
                              "image_id": i["Image"], "resources": p["resources"], "network": p["network"],
                              "environment_names": sorted(p["environment"]),
                              "permissions": p["permissions"], "gateway": p["permissions"]["jobs"],
                              "cpu_enforcement": json.loads((runtime / "limits.json").read_text()),
                              "runtime": str(runtime)}, indent=2))
        elif a.command == "exec":
            runtime, _, _ = check_managed(a.name)
            plan = json.loads((runtime / "limits.json").read_text())
            argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
            flags = ["-it"] if sys.stdin.isatty() and sys.stdout.isatty() else ["--interactive"]
            result = subprocess.run(pin_command(["podman", "exec", "--workdir=/workspace", *flags, a.name + "-agent", *(argv or ["bash"])], plan))
            sys.exit(result.returncode)
        elif a.command == "verify":
            runtime, p, _ = check_managed(a.name)
            plan = json.loads((runtime / "limits.json").read_text())
            (runtime / "host-canary").write_text("host-only isolation test\n")
            # Execute the host-owned probe via stdin; do not rely on an older image's probe.
            probe_args = ["podman", "exec", "--interactive", a.name + "-agent", "python", "-c",
                         "import sys; __package__='agent_pods'; exec(compile(sys.stdin.read(), '<host-isolation-probe>', 'exec'))",
                         "--host-home", str(Path.home()), "--host-state", str(runtime),
                         "--job-mode", p["permissions"]["jobs"],
                         *(["--expect-bootstrap"] if (runtime / "seed/pod-constitution.md").is_file() else []),
                         *(["--locked-affinity"] if plan["mode"] == "locked-affinity" else [])]
            result = run(*pin_command(probe_args, plan), capture=True, input=(ROOT / "agent_pods/probe.py").read_text())
            report = json.loads(result.stdout)
            r = p["resources"]
            expected = {"memory": size_bytes(r["memory"]), "pids": r["pids"],
                        "workspace_bytes": size_bytes(r["workspace"]), "swap": 0}
            for k, v in expected.items():
                if report["observed"][k] != v:
                    report["checks"]["enforced_" + k] = False
            if plan["mode"] == "quota":
                quota, period = report["observed"]["cpu"].split()
                report["checks"]["enforced_cpus"] = quota != "max" and int(quota) / int(period) == r["cpus"]
            else:
                report["checks"]["enforced_cpus"] = report["observed"]["affinity"] == plan["cpus"]
                init = report["observed"]["init_affinity"]
                init_cpus = []
                for part in init.split(","):
                    bounds = list(map(int, part.split("-")))
                    init_cpus.extend(range(bounds[0], bounds[-1] + 1))
                report["checks"]["enforced_init_cpus"] = init_cpus == plan["cpus"]
            report["cpu_enforcement"] = plan["mode"]
            report["checks"]["isolated_pid_namespace"] = report["observed"]["pid_namespace"] != os.readlink("/proc/self/ns/pid")
            report["passed"] = all(report["checks"].values())
            print(json.dumps(report, indent=2))
            if not report["passed"]:
                sys.exit(1)
        elif a.command == "export":
            export_workspace(a.name)
        elif a.command == "stop":
            stop(a.name)
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        if isinstance(e, subprocess.CalledProcessError) and e.stderr:
            print(e.stderr.strip(), file=sys.stderr)
        parser.exit(1, f"agent-pods: {e}\n")


if __name__ == "__main__":
    main()
