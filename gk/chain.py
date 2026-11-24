"""Hash chain helpers shared by the Lab (writer) and gk.verify (checker)."""
from __future__ import annotations

import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREREG_PATH = "prereg/PREREG.md"
CODE_FILES = ("gk/env.py", "gk/gate.py", "gk/evaluator.py", "gk/stats.py")
GENESIS = "0" * 64


def _digest(paths, root: str) -> str:
    h = hashlib.sha256()
    for p in paths:
        try:
            with open(os.path.join(root, p), "rb") as f:
                data = f.read().replace(b"\r\n", b"\n")    # stable across Windows/Linux checkouts
        except FileNotFoundError:
            data = b"<missing>"
        h.update(p.encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def prereg_sha(root: str = ROOT) -> str:
    return _digest([PREREG_PATH], root)


def code_sha(root: str = ROOT) -> str:
    return _digest(CODE_FILES, root)


def canonical(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_hash(rec: dict) -> str:
    """sha256 of the canonical JSON of the record WITHOUT its own "hash" field."""
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()
