"""Exercise both preparation backends and rejected requests in a live pod."""
import base64
import copy
import json

from agent_pods.client import request


def main():
    job = {"action": "prepare", "backend": "slurm", "entrypoint": "smoke.py",
           "files": {"smoke.py": base64.b64encode(b"print('Agentroom smoke')\n").decode()},
           "resources": {"nodes": 1, "cpus": 2, "memory_mb": 2048, "gpus": 0,
                         "walltime_seconds": 28800, "runtime_seconds": 18000,
                         "checkpoint_margin_seconds": 10800}}
    prepared = {}
    checks = {}
    for backend in ("slurm", "poorman"):
        job["backend"] = backend
        reply = request(job)
        assert reply["ok"] and not reply["live_submission"]
        status = request({"action": "status", "job_id": reply["job_id"]})
        checks[backend + "_prepared"] = status["ok"] and status["state"] == "prepared"
        prepared[backend] = reply["job_id"]
    bad = copy.deepcopy(job)
    bad["files"] = {"../escape.py": job["files"]["smoke.py"]}
    bad["entrypoint"] = "../escape.py"
    checks["traversal_rejected"] = not request(bad)["ok"]
    bad = copy.deepcopy(job)
    bad["resources"]["cpus"] = 999
    checks["excess_resources_rejected"] = not request(bad)["ok"]
    bad = copy.deepcopy(job)
    bad["backend"] = "ssh arbitrary-host"
    checks["unknown_backend_rejected"] = not request(bad)["ok"]
    checks["status_traversal_rejected"] = not request({"action": "status", "job_id": "../../etc/passwd"})["ok"]
    checks["host_command_rejected"] = not request({"action": "exec", "command": "touch /host/pwned"})["ok"]
    checks["live_submission_rejected"] = not request({"action": "submit"})["ok"]
    print(json.dumps({"passed": all(checks.values()), "checks": checks, "prepared": prepared}, indent=2))
    assert all(checks.values())


if __name__ == "__main__":
    main()
