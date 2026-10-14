# Destination worker contract

The gateway already prepares bundles for `slurm` and `poorman`. Neither backend
currently executes jobs. A host-owned adapter must satisfy this contract first:

1. Use a dedicated worker identity; keep SSH keys and cluster credentials outside
   uploaded code's filesystem. Do not run arbitrary uploads as the user's normal
   account with a shared home mounted.
2. Accept the validated manifest and files, never an uploaded shell command or
   uploaded SBATCH wrapper. Verify hashes before staging.
3. Generate the scheduler wrapper from trusted code. Enforce one node, resource
   ceilings, job quotas, application runtime and checkpoint margin. Extend the
   site's canonical renderer rather than replacing scientific run policy.
4. Start the code inside a verified isolated runtime with a read-only bundle and
   a writable job-specific results directory. Exclude shared filesystems, host
   home, runtime sockets and private network routes.
5. For Slurm, request explicit CPUs, memory, time and allowed GPU resources. For
   a workstation pool, use an approved reservation/queue mechanism and kernel
   limits; never implement scheduling by remotely running arbitrary background
   shell commands.
6. Expose status/logs/results for this gateway's own job IDs only. Cancellation
   must be limited to its own jobs. Emit audit records with resource settings,
   content hashes and final state; never include credentials.
7. Test with a small non-research job and an isolation probe before enabling
   production. GPU passthrough and internet access require separate verification.

Paderborn is Chatty's documented existing production backend. Its current
SSH/VPN path is via ws3. The second backend's actual scheduling entry point still
needs identification. Existing Chatty production jobs and launchers are untouched.
