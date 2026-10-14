"""Verify startup guidance and job permission boundaries without a runtime."""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_pods.bootstrap import install_constitution
from agent_pods.cli import launch_profile, secure_copy, start
from agent_pods.gateway import handle
from agent_pods.policy import validate_profile

ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def profile(self):
        return json.loads((ROOT / "profiles/chatty.json").read_text())

    def args(self, **overrides):
        values = dict(profile=str(ROOT / "profiles/chatty.json"), source="examples",
                      name=None, cpus=None, memory=None, workspace=None, pids=None, jobs=None)
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_bootstrap_preserves_project_and_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, seed = Path(tmp) / "source", Path(tmp) / "seed"
            source.mkdir()
            original = "Use project normalization.\n"
            (source / "AGENTS.md").write_text(original)
            (source / "CLAUDE.md").write_text("@project-rules.md\n")
            secure_copy(source, seed, 1024)
            p = self.profile()
            p["environment"]["EXAMPLE_TOKEN"] = "must-not-be-published"
            plan = {"mode": "locked-affinity", "cpus": [3, 4],
                    "seccomp": "/private/host-policy", "oci_runtime": "/private/runtime"}
            install_constitution(seed, ROOT / "pod-constitution.md", p, plan)
            self.assertEqual((source / "AGENTS.md").read_text(), original)
            self.assertIn(original, (seed / "AGENTS.md").read_text())
            self.assertIn("@project-rules.md", (seed / "CLAUDE.md").read_text())
            self.assertIn("@pod-constitution.md", (seed / "CLAUDE.md").read_text())
            settings = json.loads((seed / "pod-settings.json").read_text())
            self.assertEqual(settings["permissions"]["jobs"], "prepare")
            self.assertEqual(settings["allowed_backends"], ["poorman", "slurm"])
            raw = (seed / "pod-settings.json").read_text()
            self.assertNotIn("must-not-be-published", raw)
            self.assertNotIn("/private/", raw)
            p["permissions"]["jobs"] = "none"
            install_constitution(seed, ROOT / "pod-constitution.md", p, plan)
            self.assertEqual(json.loads((seed / "pod-settings.json").read_text())["allowed_backends"], [])

    def test_restaging_does_not_duplicate_instruction_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp)
            p = self.profile()
            plan = {"mode": "quota", "cpus": 2}
            install_constitution(seed, ROOT / "pod-constitution.md", p, plan)
            before = {name: (seed / name).read_text() for name in
                      ("AGENTS.md", "CLAUDE.md", "pod-constitution.md", "pod-settings.json")}
            self.assertEqual(install_constitution(seed, ROOT / "pod-constitution.md", p, plan), 0)
            self.assertEqual(before, {name: (seed / name).read_text() for name in before})

    def test_none_mode_denies_gateway_actions_and_writes_no_bundles(self):
        p = self.profile()
        p["permissions"]["jobs"] = "none"
        p = validate_profile(p)
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp)
            backends = handle({"action": "backends"}, p, jobs)
            self.assertEqual(backends["mode"], "none")
            self.assertEqual(backends["backends"], {})
            for action in ("prepare", "status"):
                with self.assertRaisesRegex(ValueError, "job access is disabled"):
                    handle({"action": action}, p, jobs)
            self.assertEqual(list(jobs.iterdir()), [])

    def test_v2_requires_explicit_permissions(self):
        p = self.profile()
        del p["permissions"]
        with self.assertRaises(ValueError):
            validate_profile(p)
        p = self.profile()
        p["permissions"] = {"jobs": True}
        with self.assertRaises(ValueError):
            validate_profile(p)
        for version in (True, 2.0):
            p = self.profile()
            p["schema_version"] = version
            with self.assertRaises(ValueError):
                validate_profile(p)

    def test_legacy_profile_preserves_preparation_behavior(self):
        p = self.profile()
        p["schema_version"] = 1
        del p["permissions"]
        upgraded = validate_profile(p)
        self.assertEqual(upgraded["permissions"], {"jobs": "prepare"})
        self.assertEqual(upgraded["schema_version"], 2)

    def test_cli_overrides_are_validated_and_effective(self):
        p = launch_profile(self.args(name="small-task", cpus=1, memory="3g",
                                     workspace="256m", pids=64, jobs="none"))
        self.assertEqual(p["name"], "small-task")
        self.assertEqual(p["resources"]["cpus"], 1)
        self.assertEqual(p["resources"]["memory"], "3g")
        self.assertEqual(p["resources"]["workspace"], "256m")
        self.assertEqual(p["resources"]["pids"], 64)
        self.assertEqual(p["permissions"]["jobs"], "none")
        with self.assertRaises(ValueError):
            launch_profile(self.args(cpus=0))

    def test_submit_mode_fails_before_any_runtime_call(self):
        with patch("agent_pods.cli.run") as run:
            with self.assertRaisesRegex(ValueError, "verified destination worker adapter"):
                start(self.args(jobs="submit"))
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
