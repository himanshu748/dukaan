"""Turning one generated clip into stills for every format.

The GPU runs once per pack. Everything else is derived from those frames, so
two questions have to be answered here: which frames make good stills, and how
a square-ish generated frame becomes a 9:16 story without clipping the product.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

#: Laplacian. Its response variance is the standard cheap focus measure, and it
#: is what separates a frame mid-motion from one the camera has settled on.
_LAPLACIAN = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)


def sharpness(image: Image.Image) -> float:
    a = np.asarray(image.convert("L"), dtype=np.float32) / 255.0
    lap = (
        _LAPLACIAN[0, 1] * np.roll(a, 1, 0)
        + _LAPLACIAN[2, 1] * np.roll(a, -1, 0)
        + _LAPLACIAN[1, 0] * np.roll(a, 1, 1)
        + _LAPLACIAN[1, 2] * np.roll(a, -1, 1)
        + _LAPLACIAN[1, 1] * a
    )[1:-1, 1:-1]
    return float(lap.var())


def _centre_zoom(image: Image.Image, zoom: float) -> Image.Image:
    """Crop around the product while allowing for the prompted camera push."""
    if zoom <= 1.0:
        return image
    w, h = image.size
    cw, ch = max(int(w / zoom), 1), max(int(h / zoom), 1)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    return image.crop((x0, y0, x0 + cw, y0 + ch))


def _visual_features(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Small structural and colour descriptors requiring no extra model.

    The scene background is expected to change, so the descriptor emphasises
    central edges, where the plated product lives, and gives colour only a
    small vote.  This cannot prove identity.  It is useful for rejecting an
    obviously morphed frame before sharpness alone selects it.
    """
    img = image.convert("RGB").resize((72, 72), Image.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = arr[8:-8, 8:-8]
    gray = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    gx = np.diff(gray, axis=1, append=gray[:, -1:])
    gy = np.diff(gray, axis=0, append=gray[-1:, :])
    edges = np.sqrt(gx * gx + gy * gy).reshape(-1)
    norm = float(np.linalg.norm(edges))
    if norm:
        edges /= norm

    hist = []
    for channel in range(3):
        h, _ = np.histogram(arr[..., channel], bins=12, range=(0.0, 1.0))
        hist.extend(h.astype(np.float32))
    colour = np.asarray(hist, dtype=np.float32)
    colour /= colour.sum() or 1.0
    return edges, colour


def reference_similarity(reference: Image.Image, candidate: Image.Image) -> float:
    """Return a 0..1 consistency signal tolerant of a modest camera push.

    It deliberately avoids the word "identity": only an object-aware model or
    a human review can establish that.  The maximum over three reference zooms
    stops a legitimate push-in from looking like product drift.
    """
    cand_edges, cand_colour = _visual_features(candidate)
    scores = []
    for zoom in (1.0, 1.08, 1.16):
        ref_edges, ref_colour = _visual_features(_centre_zoom(reference, zoom))
        edge = float(np.clip(np.dot(ref_edges, cand_edges), 0.0, 1.0))
        colour = float(np.minimum(ref_colour, cand_colour).sum())
        scores.append(0.8 * edge + 0.2 * colour)
    return float(max(scores))


def pick_frames(
    frames: list[Image.Image],
    k: int,
    reference: Image.Image | None = None,
) -> list[int]:
    """Pick k frames: spread across the clip, sharpest within each window.

    Spread first, because two stills lifted from adjacent frames are the same
    picture and the seller gets three copies of one creative. Sharpest within
    the window second, because a frame caught mid-push is soft, and a soft
    still is the one thing a printed banner cannot hide.

    The first frame is never eligible: it is the plate the model was given, so
    it shows none of what the model did.
    """
    if k <= 0 or not frames:
        return []
    usable = list(range(1, len(frames))) or [0]
    if k >= len(usable):
        return usable[:k] if len(usable) >= k else usable + [usable[-1]] * (k - len(usable))

    edges = np.linspace(0, len(usable), k + 1).astype(int)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        window = usable[lo:hi] or [usable[min(lo, len(usable) - 1)]]
        focus = {i: sharpness(frames[i]) for i in window}
        lo_focus, hi_focus = min(focus.values()), max(focus.values())

        def score(i: int) -> float:
            focus_score = (
                (focus[i] - lo_focus) / (hi_focus - lo_focus)
                if hi_focus > lo_focus else 1.0
            )
            if reference is None:
                return focus_score
            consistency = reference_similarity(reference, frames[i])
            # Product consistency wins a close call; sharpness still prevents
            # a motion-blurred but structurally similar frame being selected.
            return 0.75 * consistency + 0.25 * focus_score

        out.append(max(window, key=score))
    return out


def fit_scene(frame: Image.Image, size: tuple[int, int], feather: float = 24.0) -> Image.Image:
    """Place a generated frame in a format of a different shape.

    Cover-cropping is the usual move and it is wrong here: cropping a square
    frame to 9:16 cuts the sides off, and the product is what lives in the
    middle of those sides. So the frame is scaled to fit whole and the bands
    left over are filled from the frame's own content.

    They are filled by reflection, not by stretching the edge row. Stretching
    works on a plain wash and falls apart on anything with texture: extending a
    bokeh background sideways draws it into long horizontal streaks that read
    immediately as a mistake. Reflecting continues the texture instead, so the
    band looks like more of the same scene, and blurring the join hides the
    mirror line.
    """
    tw, th = size
    scale = min(tw / frame.width, th / frame.height)
    inner = frame.resize((max(int(frame.width * scale), 1), max(int(frame.height * scale), 1)),
                         Image.LANCZOS)
    if inner.size == size:
        return inner.convert("RGB")

    ox, oy = (tw - inner.width) // 2, (th - inner.height) // 2
    a = np.asarray(inner.convert("RGB"), dtype=np.uint8)
    canvas = np.empty((th, tw, 3), dtype=np.uint8)

    def reflect(idx: np.ndarray, n: int) -> np.ndarray:
        if n < 2:
            return np.zeros_like(idx)
        period = 2 * n - 2
        idx = np.mod(idx, period)
        return np.where(idx < n, idx, period - idx)

    ys = reflect(np.arange(th) - oy, inner.height)
    xs = reflect(np.arange(tw) - ox, inner.width)
    canvas[:] = a[ys[:, None], xs[None, :]]

    out = Image.fromarray(canvas, "RGB")
    if feather and (ox or oy):
        # Blur only the stretched bands, so the real frame stays crisp.
        blurred = out.filter(ImageFilter.GaussianBlur(feather))
        mask = Image.new("L", size, 255)
        mask.paste(0, (ox, oy, ox + inner.width, oy + inner.height))
        mask = mask.filter(ImageFilter.GaussianBlur(feather / 2))
        out = Image.composite(blurred, out, mask)
    return out
