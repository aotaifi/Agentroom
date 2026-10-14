"""Fail-closed host policy. Profiles are never mounted inside the sandbox."""
import base64
import math
import re


def integer(value, label, minimum=1, maximum=2**40):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def size_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*[kmg]", value):
        raise ValueError("sizes must use positive k/m/g units, for example 4g")
    return int(value[:-1]) * 1024 ** ("kmg".index(value[-1]) + 1)


def exact_keys(obj, keys, label):
    if type(obj) is not dict or set(obj) != set(keys):
        raise ValueError(f"invalid {label} fields; expected {sorted(keys)}")


def validate_profile(p):
    keys = {"schema_version", "name", "image", "network", "resources", "environment", "gateway", "permissions"}
    # Existing v1 pods were preparation-only; preserve that behavior when reading.
    if type(p) is dict and type(p.get("schema_version")) is int and p["schema_version"] == 1 and set(p) == keys - {"permissions"}:
        p = {**p, "schema_version": 2, "permissions": {"jobs": "prepare"}}
    exact_keys(p, keys, "profile")
    if type(p["schema_version"]) is not int or p["schema_version"] != 2:
        raise ValueError("unsupported profile version")
    if not isinstance(p["name"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", p["name"]):
        raise ValueError("invalid sandbox name")
    if not isinstance(p["image"], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]*", p["image"]):
        raise ValueError("invalid image reference")
    if p["network"] != "none":
        raise ValueError("this release only supports enforced offline networking")
    exact_keys(p["permissions"], {"jobs"}, "permissions")
    mode = p["permissions"]["jobs"]
    if type(mode) is not str or mode not in {"none", "prepare", "submit"}:
        raise ValueError("permissions.jobs must be none, prepare, or submit")
    if mode == "submit":
        raise ValueError("live submission requires a verified destination worker adapter; this release supports none or prepare")
    r = p["resources"]
    exact_keys(r, {"cpus", "memory", "workspace", "home", "tmp", "pids"}, "resources")
    if type(r["cpus"]) not in (int, float) or not math.isfinite(r["cpus"]) or not 0 < r["cpus"] <= 256:
        raise ValueError("cpus must be finite, positive, and <= 256")
    memory = size_bytes(r["memory"])
    writes = sum(size_bytes(r[k]) for k in ("workspace", "home", "tmp"))
    if writes + 64 * 1024**2 >= memory:
        raise ValueError("memory must exceed all writable filesystem capacities plus 64 MiB")
    integer(r["pids"], "pids", 16, 4096)
    if type(p["environment"]) is not dict or len(p["environment"]) > 64:
        raise ValueError("environment must be a small explicit map")
    for k, v in p["environment"].items():
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k) or type(v) is not str or "\x00" in v or len(v) > 4096:
            raise ValueError("invalid explicit environment variable")
        if k in {"HOME", "PATH", "AGENT_PODS_SOCKET", "LD_PRELOAD", "BASH_ENV", "ENV"}:
            raise ValueError(f"{k} is reserved by the sandbox")
    g = p["gateway"]
    exact_keys(g, {"max_jobs", "max_job_bytes", "backends"}, "gateway")
    integer(g["max_jobs"], "max_jobs", 1, 32)
    integer(g["max_job_bytes"], "max_job_bytes", 1024, 16 * 1024**2)
    if type(g["backends"]) is not dict or not 1 <= len(g["backends"]) <= 8:
        raise ValueError("configure between one and eight backends")
    for name, b in g["backends"].items():
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
            raise ValueError("invalid backend name")
        exact_keys(b, {"kind", "label", "max_cpus", "max_memory_mb", "max_gpus", "max_walltime_seconds"}, "backend")
        if b["kind"] not in {"slurm", "poorman"} or not isinstance(b["label"], str):
            raise ValueError("unsupported backend")
        for k in ("max_cpus", "max_memory_mb", "max_walltime_seconds"):
            integer(b[k], k)
        integer(b["max_gpus"], "max_gpus", 0, 8)
    return p


def validate_job(req, profile):
    if profile["permissions"]["jobs"] == "none":
        raise ValueError("job access is disabled by the host profile")
    exact_keys(req, {"action", "backend", "entrypoint", "files", "resources"}, "job")
    if req["action"] != "prepare":
        raise ValueError("only job preparation is enabled; live submissions are disabled")
    if type(req["backend"]) is not str or req["backend"] not in profile["gateway"]["backends"]:
        raise ValueError("backend is not allowed")
    backend = profile["gateway"]["backends"][req["backend"]]
    r = req["resources"]
    exact_keys(r, {"nodes", "cpus", "memory_mb", "gpus", "walltime_seconds", "runtime_seconds", "checkpoint_margin_seconds"}, "job resources")
    integer(r["nodes"], "nodes", 1, 1)
    integer(r["cpus"], "cpus", 1, backend["max_cpus"])
    integer(r["memory_mb"], "memory_mb", 1, backend["max_memory_mb"])
    integer(r["gpus"], "gpus", 0, backend["max_gpus"])
    integer(r["walltime_seconds"], "walltime", 1, backend["max_walltime_seconds"])
    integer(r["runtime_seconds"], "runtime")
    integer(r["checkpoint_margin_seconds"], "checkpoint margin")
    if r["checkpoint_margin_seconds"] < max(10800, math.ceil(r["walltime_seconds"] * 0.1)):
        raise ValueError("checkpoint margin must be >= max(3 hours, 10% of walltime)")
    if r["runtime_seconds"] + r["checkpoint_margin_seconds"] > r["walltime_seconds"]:
        raise ValueError("application runtime plus checkpoint margin exceeds walltime")
    files = req["files"]
    if type(files) is not dict or not 1 <= len(files) <= 32:
        raise ValueError("a job needs one to 32 explicitly selected regular files")
    decoded = {}
    total = 0
    for name, content in files.items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.=-]{0,127}", name):
            raise ValueError("file names must be flat, safe names without traversal")
        if name in {"manifest.json", "submission-preview.sbatch", "submission-preview.json"}:
            raise ValueError("bundle file name is reserved by the gateway")
        if type(content) is not str:
            raise ValueError("file content must be base64")
        blob = base64.b64decode(content, validate=True)
        total += len(blob)
        if total > profile["gateway"]["max_job_bytes"]:
            raise ValueError("job bundle exceeds host policy byte limit")
        decoded[name] = blob
    if type(req["entrypoint"]) is not str or req["entrypoint"] not in decoded or not req["entrypoint"].endswith(".py"):
        raise ValueError("entrypoint must be a bundled Python file")
    return decoded
