"""Attack-oriented policy and gateway regression tests; no container required."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_pods.cli import container_args, secure_copy
from agent_pods.gateway import handle
from agent_pods.policy import validate_job, validate_profile

ROOT = Path(__file__).resolve().parents[1]


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((ROOT / "profiles/chatty.json").read_text())
        self.job = {
            "action": "prepare", "backend": "slurm", "entrypoint": "main.py",
            "files": {"main.py": base64.b64encode(b"print('hello')\n").decode()},
            "resources": {"nodes": 1, "cpus": 2, "memory_mb": 2048, "gpus": 0,
                          "walltime_seconds": 28800, "runtime_seconds": 18000,
                          "checkpoint_margin_seconds": 10800}}

    def test_profiles_are_valid(self):
        for file in (ROOT / "profiles").glob("*.json"):
            validate_profile(json.loads(file.read_text()))

    def test_privilege_and_network_changes_fail_closed(self):
        for key, value in (("privileged", True), ("volumes", ["/:/host"])):
            p = copy.deepcopy(self.profile)
            p[key] = value
            with self.assertRaises(ValueError):
                validate_profile(p)
        for network in ("host", "bridge", "slirp4netns", None):
            p = copy.deepcopy(self.profile)
            p["network"] = network
            with self.assertRaises(ValueError):
                validate_profile(p)

    def test_invalid_limits_and_reserved_environment_rejected(self):
        for field, value in (("cpus", float("nan")), ("cpus", 0), ("cpus", True),
                             ("memory", "0"), ("memory", "1m"), ("pids", 0)):
            p = copy.deepcopy(self.profile)
            p["resources"][field] = value
            with self.assertRaises(ValueError):
                validate_profile(p)
        self.profile["environment"]["BASH_ENV"] = "/seed/attack.sh"
        with self.assertRaises(ValueError):
            validate_profile(self.profile)

    def test_shell_metacharacters_are_not_container_flags(self):
        p = copy.deepcopy(self.profile)
        p["environment"]["EXAMPLE"] = "$(touch /host/pwned); --privileged"
        validate_profile(p)
        args = container_args(p, Path("/private/test"))
        self.assertIn("EXAMPLE=$(touch /host/pwned); --privileged", args)
        self.assertNotIn("--privileged", args)
        self.assertIn("--cap-drop=ALL", args)
        self.assertIn("--read-only", args)

    def test_file_traversal_and_reserved_names_rejected(self):
        for name in ("../escape.py", "/tmp/escape.py", "sub/main.py", "a;sh.py", "manifest.json"):
            job = copy.deepcopy(self.job)
            job["files"] = {name: self.job["files"]["main.py"]}
            job["entrypoint"] = name
            with self.assertRaises(ValueError):
                validate_job(job, self.profile)

    def test_resource_and_backend_attacks_rejected(self):
        for field, value in (("nodes", 2), ("memory_mb", 0), ("cpus", 999), ("gpus", 2),
                             ("checkpoint_margin_seconds", 0), ("runtime_seconds", 28800)):
            job = copy.deepcopy(self.job)
            job["resources"][field] = value
            with self.assertRaises(ValueError):
                validate_job(job, self.profile)
        self.job["backend"] = "ssh attacker@host"
        with self.assertRaises(ValueError):
            validate_job(self.job, self.profile)

    def test_submit_cannot_execute_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp)
            with self.assertRaisesRegex(ValueError, "live submissions are disabled"):
                handle({"action": "submit", "command": "touch /tmp/escape"}, self.profile, jobs)
            self.assertEqual(list(jobs.iterdir()), [])

    def test_preparation_is_immutable_bounded_and_nonexecuting(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp)
            marker = jobs / "must-not-exist"
            self.job["files"]["main.py"] = base64.b64encode(f"open({str(marker)!r},'w').write('bad')".encode()).decode()
            self.profile["gateway"]["max_jobs"] = 1
            reply = handle(self.job, self.profile, jobs)
            self.assertFalse(marker.exists())
            self.assertFalse(reply["live_submission"])
            self.assertTrue(reply["requires_isolated_worker"])
            self.assertEqual((jobs / reply["job_id"] / "main.py").stat().st_mode & 0o777, 0o400)
            status = handle({"action": "status", "job_id": reply["job_id"]}, self.profile, jobs)
            self.assertEqual(status["state"], "prepared")
            with self.assertRaisesRegex(ValueError, "quota"):
                handle(self.job, self.profile, jobs)

    def test_poorman_is_same_policy_boundary(self):
        self.job["backend"] = "poorman"
        with tempfile.TemporaryDirectory() as tmp:
            reply = handle(self.job, self.profile, Path(tmp))
            self.assertEqual(reply["backend_kind"], "poorman")
            self.assertFalse(reply["live_submission"])
            self.assertTrue((Path(tmp) / reply["job_id"] / "submission-preview.json").exists())

    def test_bundle_size_and_status_traversal_rejected(self):
        self.profile["gateway"]["max_job_bytes"] = 4
        with self.assertRaisesRegex(ValueError, "byte limit"):
            validate_job(self.job, self.profile)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "invalid job ID"):
                handle({"action": "status", "job_id": "../../etc/passwd"}, self.profile, Path(tmp))

    def test_seed_symlinks_rejected_and_secrets_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            dst = root / "dst"
            src.mkdir()
            (src / ".env").write_text("secret")
            (src / "main.py").write_text("print('ok')")
            secure_copy(src, dst, 1024)
            self.assertFalse((dst / ".env").exists())
            self.assertTrue((dst / "main.py").exists())
            (src / "attack.py").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(ValueError, "regular"):
                secure_copy(src, root / "next", 1024)


if __name__ == "__main__":
    unittest.main()
