"""Read-only Chatty example importer; writes a private build context and seed."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def copy_files(source, target, suffixes, exclude=()):
    total = 0
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if path.is_symlink() or any(part.startswith(".") or part in exclude for part in rel.parts):
            continue
        if path.is_file() and (path.suffix in suffixes or path.name in {"LICENSE", "CITATION.cff"}):
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            total += path.stat().st_size
    return total


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chatty", type=Path, required=True)
    p.add_argument("--bench", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    workspace = a.output / "seed"
    workspace.mkdir()
    copy_files(a.chatty, workspace / "Chatty", {".py", ".sh", ".md", ".sbatch"},
               {"results", "jobs", "security-test", "tools", "__pycache__"})
    bench = workspace / "j1j2_vit_bench"
    copy_files(a.bench, bench, {".py"}, {"nqsmagic", "ising-glass-annealer", "__pycache__"})
    for name in ("vit_J2=0.50_N=6x6_k=0.mpack", "vit_J2=0.50_N=8x8_k=0.mpack",
                 "energy_krylov_vs_vit_6x6_indep.npz", "gr_6x6_population_scaling_M128.npz",
                 "krylov_scaling_6x6_a1.20_tr4096_va2048.npz"):
        source = a.bench / name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"required input missing or unsafe: {name}")
        shutil.copyfile(source, bench / name)
    vendor = a.output / "vendor/nqsmagic"
    copy_files(a.bench / "nqsmagic", vendor, {".py", ".toml", ".cfg", ".md", ".cff"},
               {"test", "examples", "__pycache__", "nqsmagic.egg-info"})
    revision = subprocess.check_output(["git", "-C", str(a.bench / "nqsmagic"), "rev-parse", "HEAD"], text=True).strip()
    (a.output / "snapshot.json").write_text(json.dumps({
        "nqsmagic_revision": revision, "source_modified": False,
        "contains": "selected research code, five active inputs, and local nqsmagic package source",
        "excluded": ["SSH keys", "credentials", "Git metadata", "virtualenvs", "job logs", "mail state"],
        "publication": "private local runtime asset; never commit this directory"}, indent=2) + "\n")
    print(json.dumps({"output": str(a.output), "vendor_revision": revision}))


if __name__ == "__main__":
    main()
