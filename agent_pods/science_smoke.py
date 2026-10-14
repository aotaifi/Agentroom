"""Small CPU/serialization smoke test; never runs a research campaign."""
import hashlib
import json
from pathlib import Path
import importlib.metadata as metadata

import flax.serialization
import jax
import jax.numpy as jnp
import numpy as np
from numba import njit
from scipy.sparse import csr_matrix
from nqsmagic.models import ViT
from nqsmagic.utils import _logpsi_transl_2d


def main():
    @njit
    def total(a):
        return a.sum()

    result = float(jax.jit(lambda x: jnp.sum(x * x))(jnp.arange(8.0)).block_until_ready())
    if result != 140.0 or total(np.arange(8)) != 28 or csr_matrix(np.eye(3)).nnz != 3:
        raise ValueError("scientific compute check failed")
    checkpoints = {}
    for name in ("vit_J2=0.50_N=6x6_k=0.mpack", "vit_J2=0.50_N=8x8_k=0.mpack"):
        data = (Path("/workspace/j1j2_vit_bench") / name).read_bytes()
        restored = flax.serialization.msgpack_restore(data)
        if not isinstance(restored, dict) or not restored:
            raise ValueError("invalid model checkpoint")
        checkpoints[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "decoded": True}
    names = ("numpy", "scipy", "jax", "jaxlib", "NetKet", "flax", "optax", "numba", "reportlab", "nqsmagic")
    print(json.dumps({"passed": True, "jax_backend": jax.default_backend(),
                      "versions": {name: metadata.version(name) for name in names},
                      "jit_sum_squares": result, "numba_sum": 28, "scipy_sparse_nnz": 3,
                      "nqsmagic_imported": ViT is not None and callable(_logpsi_transl_2d),
                      "checkpoints": checkpoints}, indent=2))


if __name__ == "__main__":
    main()
