"""Host-side startup instructions and a non-secret view of the effective profile."""
import json
from pathlib import Path

START = "<!-- agentroom:startup-instructions -->"
END = "<!-- /agentroom:startup-instructions -->"


def install_constitution(seed, template, profile, plan):
    seed = Path(seed)
    settings = {
        "schema_version": 1,
        "name": profile["name"],
        "image": profile["image"],
        "resources": profile["resources"],
        "network": profile["network"],
        "permissions": profile["permissions"],
        "allowed_backends": (sorted(profile["gateway"]["backends"])
                             if profile["permissions"]["jobs"] == "prepare" else []),
        "environment_names": sorted(profile["environment"]),
        "job_limits": profile["gateway"],
        "live_submission_available": False,
        "cpu_enforcement": {k: plan[k] for k in ("mode", "cpus")},
        "note": "Informational copy. Host-owned policy and kernel limits enforce permissions.",
    }
    content = {
        "pod-constitution.md": Path(template).read_text(),
        "pod-settings.json": json.dumps(settings, indent=2) + "\n",
    }
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = seed / name
        original = path.read_text() if path.exists() else ""
        if original.startswith(START + "\n"):
            _, marker, tail = original.partition(END + "\n")
            if not marker:
                raise ValueError("incomplete Agentroom instruction header in source")
            original = tail.lstrip("\n")
        pointer = ("Read [pod-constitution.md](pod-constitution.md) and "
                   "[pod-settings.json](pod-settings.json) before starting research.\n"
                   if name == "AGENTS.md" else "@pod-constitution.md\n"
                   "Read pod-settings.json for this pod's allocation and permissions.\n")
        content[name] = START + "\n" + pointer + END + "\n"
        if original:
            content[name] += "\n" + original
    added = 0
    for name, body in content.items():
        path = seed / name
        before = path.stat().st_size if path.exists() else 0
        path.write_text(body)
        path.chmod(0o644)
        added += path.stat().st_size - before
    return added
