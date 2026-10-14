# Agentroom

Start a bounded research workspace from one host-owned profile. The launcher
installs the constitution and agent pointers automatically, copies selected
project files, and enforces resource limits and job permissions.

## Start a pod

Run these commands on a Linux host such as ws1, as your regular user.
Requirements: rootless Podman, cgroup v2 with memory/process delegation, Python
3.9+, and local container storage. For CPU affinity fallback, use crun with OCI
execCPUAffinity support (verified on ws1 with crun 1.27).

1. Build the environment once:

   ```sh
   ./agent-pods build base
   ```

2. Choose a profile. Copy a template and edit its image, resources, environment,
   job permission and backend caps:

   ```sh
   cp profiles/generic.json profiles/my-task.json
   ```

3. Launch a task. Common choices can also override the profile on the command line:

   ```sh
   ./agent-pods start --profile profiles/my-task.json --source examples \
     --name my-task --cpus 2 --memory 4g --workspace 768m --pids 128 --jobs none
   ```

   Use a unique name for each task. The effective profile is validated and saved
   outside the agent before any container is launched.

4. Verify the pod and open its shell:

   ```sh
   ./agent-pods verify my-task
   ./agent-pods exec my-task -- bash
   ```

5. Preserve results and stop when finished:

   ```sh
   ./agent-pods export my-task
   ./agent-pods stop my-task
   ```

   Stop exports /workspace before removing the pod. Home and temporary files are
   discarded. Exports are agent-controlled archives and are never unpacked on
   the host automatically. Workspace edits are temporary until exported.

## What startup installs

Every new pod starts with these files already in /workspace:

| File | Purpose |
|---|---|
| pod-constitution.md | Shared physics research guide: validation, reproducibility, bounded runs, handoff |
| AGENTS.md | Points Codex-style agents to the constitution and effective settings |
| CLAUDE.md | Imports the constitution for Claude |
| pod-settings.json | Actual resource allocation, job mode, allowed backends and caps |

The host's constitution template is authoritative for startup. The launcher
preserves existing root project instructions beneath its generated pointers.
Repeated staging replaces the generated pointer header rather than duplicating it.
The constitution and settings filenames are reserved and regenerated from trusted
host inputs. Originals are available read-only under /seed; editing workspace
copies cannot change kernel limits or gateway policy. No manual copying is needed.

## Choose resources and environment

| Profile field | Meaning |
|---|---|
| resources.cpus | CPU ceiling; fractional values require delegated quotas |
| resources.memory | Pod RAM ceiling, e.g. 4g; swap is disabled |
| resources.workspace / home / tmp | Writable filesystem capacities |
| resources.pids | Agent process/thread ceiling |
| image | Prebuilt dependency environment |
| environment | Explicit environment variables, e.g. thread counts |
| permissions.jobs | Job permission: none, prepare, or submit |
| gateway.max_jobs | Maximum retained prepared bundles per pod |
| gateway.max_job_bytes | Maximum uploaded bytes per bundle |
| gateway.backends | Allowed destinations and their per-job CPU/RAM/GPU/time ceilings |

CPU quotas are used when delegated. Otherwise, whole-CPU requests use a private
crun shim that pins init and later execs; seccomp denies affinity changes. Startup
checks both masks. This limits CPU consumption without reserving exclusive cores.
Memory must exceed the combined writable capacities plus runtime headroom.

Profiles are host-owned. Change settings by stopping and recreating the pod.
AGENT_PODS_STATE selects its host state directory; ws1 defaults to local scratch.
Network permissions in this release are fixed to offline. Privileged mode,
arbitrary host mounts, host networking, runtime sockets, and agent-edited policy
are rejected. New permission types require an enforcing implementation.

## Choose job permissions and caps

| permissions.jobs | Behavior |
|---|---|
| none | Job preparation and status access denied; backend list is empty |
| prepare | Allowed backend requests produce immutable bundles and previews |
| submit | Startup rejected: verified isolated destination adapters are not implemented yet |

For a preparation-enabled pod, launch with --jobs prepare. Inside it:

```sh
jobctl backends
jobctl prepare smoke.py --backend slurm --cpus 2 --memory-mb 2048
jobctl prepare smoke.py --backend poorman --cpus 2 --memory-mb 2048
jobctl status job-REPLACE_WITH_ID
```

Only configured backends are permitted. Each has max_cpus, max_memory_mb,
max_gpus and max_walltime_seconds. Requests must use one node, explicit memory,
and application runtime plus checkpoint margin within walltime. The current
checkpoint margin must be at least max(3 hours, 10% of walltime).

The default gateway admits eight retained bundles of at most 16 MiB each.
These are preparation/storage caps, not concurrent running-job slots. Uploaded
files use flat safe names; traversal, shell commands and uploaded SBATCH execution
are rejected. jobctl submit fails closed. Live execution needs the
[verified destination worker contract](docs/WORKERS.md), including scheduler
identity, isolation, running-job quotas and cancellation restricted to owned jobs.
A configuration flag alone cannot enable live submission.

Schema v2 makes permissions explicit. Existing v1 profiles are read as
preparation-only to preserve their original behavior.

## Chatty example

On the Mac, stage a private source/input snapshot:

```sh
python3 -m agent_pods.stage_chatty \
  --chatty "$HOME/Chatty" --bench "$HOME/j1j2_vit_bench" \
  --output .private/chatty-snapshot
```

Copy the snapshot to the build host. Copy its vendor/nqsmagic directory to
.private/vendor/nqsmagic there, build base, then build the Chatty image:

```sh
./agent-pods build chatty
./agent-pods start --profile profiles/chatty.json \
  --source .private/chatty-snapshot/seed --name chatty-task --jobs prepare
./agent-pods verify chatty-task
./agent-pods exec chatty-task -- bash
```

The selected files are copied; original Chatty remains untouched. Python 3.11
scientific dependencies are pinned in images/chatty/requirements.txt. Local
nqsmagic source and model inputs remain private. Mac automation and SSH/VPN helpers
stay on their existing hosts. See [the ws1 demo](docs/DEMO.md).

## Bind the agent and verify

Bind the agent's terminal and file tools to agent-pods exec. If the agent runner
is outside the pod, have it read /workspace/AGENTS.md through that executor as
startup context; filesystem installation alone does not load an external model's
context. Also giving it an
unrestricted host shell, SSH, Desktop Commander or a runtime socket bypasses this
boundary. The prototype provides a workspace; it does not start an AI model or
provision model credentials. Rootless containers share the host kernel.

```sh
python3 -m unittest discover -s tests -v
./agent-pods verify my-task
```

Checks cover enforced resource limits, privilege restrictions, mounts, network
denial, installed instructions and the gateway's effective permission mode.
See [the design](docs/DESIGN.md) and [the constitution](pod-constitution.md).