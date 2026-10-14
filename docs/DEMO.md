# Chatty prototype on ws1

Agentroom's first demo was verified as the `chatty-startup` rootless Podman pod,
with an agent workspace and a separate preparation gateway. The settings and
checks below record that prototype setup. No AI model was started; an agent's
terminal/file tools must execute through the CLI.

Observed on 14 October 2026: both `chatty-startup` and `chatty-demo` were stopped
(`Exited`). This document is a setup record; use the status commands below for
current runtime state. Publishing the toolkit does not start the pods.

| Setting | Verified value |
|---|---|
| CPU | Two logical CPUs, IDs 3 and 4; init and later execs pinned; changes denied |
| Memory | 4 GiB pod ceiling, zero swap |
| Processes | 128 in the agent container |
| Workspace | 768 MiB writable tmpfs; private copy of selected Chatty inputs |
| Home / temporary files | 128 MiB each; discarded at stop |
| Runtime | Rootless Podman 5.8.2, crun 1.27, SELinux enabled |
| Identity / privileges | UID 1000, no capabilities, no new privileges, seccomp |
| Network | Offline; outbound connection denied |
| Dependencies | Python 3.11; pinned JAX, NetKet, Flax, SciPy, Numba, local nqsmagic |
| Jobs | Slurm and poorman preparation verified; live execution disabled |

From the Mac, open the sandbox:

```sh
ssh -t ws1 'cd /scratch-local/$USER/agent-pods-prototype && ./agent-pods exec chatty-startup -- bash'
```

Operator commands on ws1:

```sh
cd /scratch-local/$USER/agent-pods-prototype
./agent-pods status chatty-startup
./agent-pods verify chatty-startup
./agent-pods export chatty-startup
# Exports the workspace and then removes this pod:
./agent-pods stop chatty-startup
```

Profiles live in `profiles/chatty.json` and `profiles/generic.json`. Copy a profile,
edit its resources/image/environment/backend ceilings, then start under a unique
name. Permission changes are constrained by `agent_pods/policy.py`: this release
accepts source snapshots and offline networking only. New permission types need
an enforcing implementation and an isolation check.

Host state is in `/scratch-local/$USER/agent-pods-state`. On the Mac, the toolkit
and private acceptance report are in `~/agent-pods`. Original Chatty repositories,
research jobs, SSH/VPN configuration and Mac automation were not changed.

Validation passed: 18 policy and startup tests, live isolation checks on Chatty
and a separate generic pod, JAX/Numba computation, SciPy sparse operations, both
checkpoint decoders, two preparation backends and rejected unsafe requests.
The generic pod was exported and removed after verification. The Chatty demo
pods were retained for inspection.

Next integration step: implement isolated destination workers for Paderborn and
the workstation pool, verify their boundaries, then connect Chatty's launchers.
The pool's actual queue entry point still needs identification. This prototype
does not make live submissions to either cluster.

The current launcher automatically installs pod-constitution.md, AGENTS.md,
CLAUDE.md and pod-settings.json during startup. Choose CPU/RAM/workspace/process
limits and jobs mode with the profile or start command overrides. The off-mode
test also confirmed that editing workspace settings cannot enable jobs.

The earlier chatty-demo was retained without a restart during the startup change.
Existing pods are not automatically upgraded; the updated startup behavior was
verified with chatty-startup. The temporary off-mode test pod was exported and
removed.

Image simplification: there is now one image recipe, images/base/Containerfile.
The dependencies in the table above describe the earlier validated scientific
image. New Chatty workspaces use the base recipe with project packages added
explicitly; the optional scientific requirements are in examples/chatty/requirements.txt.

Base programming validation on 14 October 2026 passed: NumPy 2.4.6, pytest 9.1.1,
virtual environments, C compilation, non-root local pip installation, and 29 live
isolation checks. This used a fresh isolated Podman cache because ws1's existing
cache failed with a missing /bin/sh. The verification cache is selected with
CONTAINERS_STORAGE_CONF=/scratch-local/a.otaifi/agentroom-base-validation/storage.conf.
