# Agentroom — freedom inside, control outside

## 1. Problem
Agents need a real development environment. Your cluster needs a boundary that
does not depend on an agent obeying instructions.

## 2. Product
One command. One task. One disposable pod.
Same shell, code and dependencies; private workspace and kernel-enforced limits.

## 3. Control panel
One JSON profile selects CPU, RAM, writable capacity, processes, dependencies,
explicit environment variables and submission ceilings.
Secure baseline: offline, non-root, read-only image, no keys or host sockets.

## 4. First customer: Chatty
Python 3.11; pinned JAX/NetKet/Flax scientific stack; private source and input
snapshot. Chatty stays canonical and untouched.
Slurm and workstation-pool bundles cross a structured host-owned gateway.

## 5. Rollout
Now: live local pod, isolation probe and two-backend submission previews.
Next: verified isolated workers on both clusters; then integrate into Chatty.
The same launcher and profile schema support the next project.
