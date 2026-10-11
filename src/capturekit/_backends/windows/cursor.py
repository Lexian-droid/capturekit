"""Software cursor compositing for DXGI duplication (which excludes the cursor)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

POINTER_MONOCHROME = 1
POINTER_COLOR = 2
POINTER_MASKED_COLOR = 4

Buffer = npt.NDArray[np.uint8]


class CursorShape:
    def __init__(self, kind: int, width: int, height: int, pitch: int, data: bytes) -> None:
        self.kind = kind
        self.width = width
        self.height = height
        self.pitch = pitch
        self.data = data
        row_bytes = (width + 7) // 8 if kind == POINTER_MONOCHROME else width * 4
        if width <= 0 or height <= 0 or pitch < row_bytes or pitch * height > len(data):
            raise ValueError(f"malformed pointer shape {kind} {width}x{height} pitch {pitch}")
        raw = np.frombuffer(data, dtype=np.uint8)
        if kind == POINTER_MONOCHROME:
            # height covers AND mask followed by XOR mask, 1 bpp
            h = height // 2
            bits = np.unpackbits(raw[: pitch * height].reshape(height, pitch), axis=1)[:, :width]
            self.and_mask = bits[:h].astype(bool)
            self.xor_mask = bits[h:].astype(bool)
            self.height = h
            self.bgra = None
        else:
            self.bgra = (
                raw[: pitch * height]
                .reshape(height, pitch)[:, : width * 4]
                .reshape(height, width, 4)
            )


def composite(out: Buffer, shape: CursorShape, x: int, y: int) -> None:
    """Draw ``shape`` with its top-left at ``(x, y)`` in ``out`` (clipped)."""
    h, w = shape.height, shape.width
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(out.shape[1], x + w), min(out.shape[0], y + h)
    if x0 >= x1 or y0 >= y1:
        return
    dst = out[y0:y1, x0:x1, :3]
    sy, sx = slice(y0 - y, y1 - y), slice(x0 - x, x1 - x)
    if shape.kind == POINTER_MONOCHROME:
        and_m = shape.and_mask[sy, sx]
        xor_m = shape.xor_mask[sy, sx]
        res = np.where(and_m[..., None], dst, 0).astype(np.uint8)
        res = np.where(xor_m[..., None], res ^ 0xFF, res).astype(np.uint8)
        dst[...] = res
        return
    assert shape.bgra is not None
    src = shape.bgra[sy, sx]
    if shape.kind == POINTER_COLOR:
        a = src[..., 3:4].astype(np.uint16)
        blended = (src[..., :3].astype(np.uint16) * a + dst.astype(np.uint16) * (255 - a)) // 255
        dst[...] = blended.astype(np.uint8)
    else:  # masked colour: alpha 0 → replace, 0xFF → XOR
        mask = src[..., 3:4] == 0xFF
        dst[...] = np.where(mask, dst ^ src[..., :3], src[..., :3])
