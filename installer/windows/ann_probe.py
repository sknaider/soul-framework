#!/usr/bin/env python3
"""Exercise the native USearch engine in a disposable child process."""

from __future__ import annotations

import numpy as np

from usearch.index import Index


def main() -> int:
    index = Index(ndim=3, metric="cos", dtype="f32")
    vector = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    index.add(1, vector)
    matches = index.search(vector, 1)
    if len(matches) != 1 or int(matches.keys[0]) != 1:
        raise RuntimeError("USearch devolvio un resultado inesperado")
    del index
    print("USEARCH_OPERATION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
