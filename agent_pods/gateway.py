"""Host-owned gateway sidecar. It never executes submitted code or SSH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
import uuid

from .client import MAX_MESSAGE
from .policy import validate_job, validate_profile


def handle(req, profile, jobs):
    if type(req) is not dict:
        raise ValueError("request must be an object")
    if req.get("action") == "submit":
        raise ValueError("live submissions are disabled; trusted isolated workers are not installed")
    if req.get("action") == "backends" and set(req) == {"action"}:
        mode = profile["permissions"]["jobs"]
        return {"ok": True, "mode": mode,
                "backends": profile["gateway"]["backends"] if mode == "prepare" else {}}
    if profile["permissions"]["jobs"] == "none":
        raise ValueError("job access is disabled by the host profile")
    if req.get("action") == "status" and set(req) == {"action", "job_id"}:
        jid = req["job_id"]
        if type(jid) is not str or not re.fullmatch(r"job-[0-9a-f]{32}", jid):
            raise ValueError("invalid job ID")
        return {"ok": True, **json.loads((jobs / jid / "manifest.json").read_text())}
    decoded = validate_job(req, profile)
    if len(list(jobs.glob("job-*"))) >= profile["gateway"]["max_jobs"]:
        raise ValueError("job quota reached; the host operator must archive or remove old bundles")
    jid = "job-" + uuid.uuid4().hex
    manifest = {
        "job_id": jid, "state": "prepared", "backend": req["backend"],
        "backend_kind": profile["gateway"]["backends"][req["backend"]]["kind"],
        "entrypoint": req["entrypoint"], "resources": req["resources"],
        "requires_isolated_worker": True, "live_submission": False,
        "created_unix": time.time(),
        "files": {name: {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
                  for name, blob in decoded.items()},
        "worker_contract": {
            "uid": "dedicated job identity", "network": "none",
            "mounts": "job bundle read-only; job results writable; no shared home or SSH keys",
            "scheduler": "host-owned adapter; uploaded shell and sbatch files are not executed"
        }
    }
    # Only flat regular files are materialized, in a new host-owned directory.
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=jobs) as tmp:
        stage = Path(tmp) / jid
        stage.mkdir(mode=0o700)
        for name, blob in decoded.items():
            with (stage / name).open("xb") as f:
                f.write(blob)
            (stage / name).chmod(0o400)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (stage / "manifest.json").chmod(0o400)
        if manifest["backend_kind"] == "slurm":
            r = req["resources"]
            h, rem = divmod(r["walltime_seconds"], 3600)
            m, s = divmod(rem, 60)
            text = ("#!/bin/sh\n# PREVIEW ONLY. A trusted isolated worker must be installed first.\n"
                    f"#SBATCH --nodes=1\n#SBATCH --cpus-per-task={r['cpus']}\n"
                    f"#SBATCH --mem={r['memory_mb']}M\n#SBATCH --time={h:02}:{m:02}:{s:02}\n")
            if r["gpus"]:
                text += f"#SBATCH --gres=gpu:{r['gpus']}\n"
            text += "printf 'Live execution is disabled: isolated worker adapter required.\\n' >&2\nexit 78\n"
            (stage / "submission-preview.sbatch").write_text(text)
        else:
            (stage / "submission-preview.json").write_text(json.dumps({
                "kind": "poorman", "state": "adapter-required", "manifest": "manifest.json",
                "resources": req["resources"], "executes_uploaded_shell": False}, indent=2) + "\n")
        stage.rename(jobs / jid)
    return {"ok": True, **manifest}


def serve(profile_path, runtime):
    os.umask(0o077)
    profile = validate_profile(json.loads(Path(profile_path).read_text()))
    runtime = Path(runtime)
    jobs = runtime / "jobs"
    jobs.mkdir(mode=0o700, parents=True, exist_ok=True)
    sockdir = runtime / "socket"
    sockdir.mkdir(mode=0o700, exist_ok=True)
    path = sockdir / "gateway.sock"
    if path.exists():
        raise ValueError("gateway socket already exists; inspect the previous process before restarting")
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(4)
        try:
            while True:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(3)
                    try:
                        raw = bytearray()
                        while not raw.endswith(b"\n"):
                            chunk = conn.recv(65536)
                            if not chunk or len(raw) + len(chunk) > MAX_MESSAGE:
                                raise ValueError("invalid or oversized request")
                            raw.extend(chunk)
                        response = handle(json.loads(raw), profile, jobs)
                    except (ValueError, OSError, TypeError, KeyError) as e:
                        response = {"ok": False, "error": str(e)}
                    try:
                        conn.sendall(json.dumps(response).encode() + b"\n")
                    except OSError:
                        pass
        finally:
            path.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--runtime", required=True)
    a = p.parse_args()
    serve(a.profile, a.runtime)


if __name__ == "__main__":
    main()
