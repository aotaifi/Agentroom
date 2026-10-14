#!/usr/bin/python3
"""Host-only crun shim: pin init with an OCI hook and exec with OCI affinity.

The controller copies this file into its private state directory. Agents never
mount it. Keep the real runtime's inherited file descriptors and exit behavior.
"""
import json
import os
from pathlib import Path
import sys


def option(args, *names, default=None):
    for i, arg in enumerate(args):
        for name in names:
            if arg == name:
                return args[i + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return default


def main():
    here = Path(__file__).resolve()
    cpus = json.loads((here.parent / "limits.json").read_text())["cpus"]
    if sys.argv[1:] == ["--agentroom-pin"]:
        state = json.load(sys.stdin)
        os.sched_setaffinity(int(state["pid"]), cpus)
        if sorted(os.sched_getaffinity(int(state["pid"]))) != cpus:
            raise RuntimeError("container init affinity was not enforced")
        return
    args = sys.argv[1:]
    if "create" in args or "run" in args:
        bundle = Path(option(args, "--bundle", "-b", default="."))
        config = bundle / option(args, "--config", "-f", default="config.json")
        spec = json.loads(config.read_text())
        spec.setdefault("hooks", {}).setdefault("prestart", []).append({
            "path": str(here), "args": [str(here), "--agentroom-pin"], "timeout": 10})
        config.write_text(json.dumps(spec))
    if "exec" in args:
        process = option(args, "--process", "-p")
        if process is None:
            raise RuntimeError("locked affinity requires an OCI process document")
        path = Path(process)
        spec = json.loads(path.read_text())
        mask = ",".join(map(str, cpus))
        spec["execCPUAffinity"] = {"initial": mask, "final": mask}
        path.write_text(json.dumps(spec))
    os.execv("/usr/bin/crun", ["crun", *args])


if __name__ == "__main__":
    main()
