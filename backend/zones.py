"""Per-rule zone storage: the candidate masks SAM found at confirmation and the frame they came from.

Stored under DATA_DIR/rules/<rule_id>/ (gitignored): zones.npz (bool masks, packed) and
reference.jpg. The chosen instance is rules.zone_instance (1-based). Kept on disk so an armed rule
survives a restart without another SAM call.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def rule_dir(data_path: Path, rule_id: int) -> Path:
    return data_path / "rules" / str(rule_id)


def save_candidates(data_path: Path, rule_id: int, frame_jpeg: bytes, masks: list[np.ndarray]) -> None:
    d = rule_dir(data_path, rule_id)
    d.mkdir(parents=True, exist_ok=True)
    stack = np.stack(masks) if masks else np.zeros((0, 1, 1), dtype=bool)
    np.savez_compressed(d / "zones.npz", packed=np.packbits(stack, axis=-1), shape=np.array(stack.shape))
    (d / "reference.jpg").write_bytes(frame_jpeg)


def load_candidates(data_path: Path, rule_id: int) -> list[np.ndarray]:
    path = rule_dir(data_path, rule_id) / "zones.npz"
    if not path.exists():
        return []
    with np.load(path) as f:
        shape = tuple(int(x) for x in f["shape"])
        stack = np.unpackbits(f["packed"], axis=-1, count=shape[-1]).astype(bool).reshape(shape)
    return list(stack)


def reference_frame(data_path: Path, rule_id: int) -> bytes | None:
    path = rule_dir(data_path, rule_id) / "reference.jpg"
    return path.read_bytes() if path.exists() else None
