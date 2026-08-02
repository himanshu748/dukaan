"""One photo in, a shop's worth of creatives out.

The pipeline, end to end:

    photo -> cutout -> plate -> ONE GPU pass -> frames + audio
                                             |
                                             +-> pick a still per format
                                             |     -> compose type -> creatives
                                             +-> clip

The GPU runs once per pack, not once per format. That is not only a cost
decision: LTX-2.3 is the only generative model on the instance and it is a
video model, so a pass produces a moving scene rather than a picture. Stills
are lifted out of that scene, which is why three formats can look like three
different shots without three generations.

Type is composited afterwards and never generated. Models garble text, and a
seller's price and phone number are the two things on the creative that must be
exactly right.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from .backend import Backend, Clip, _fit
from .config import Config
from .frames import fit_scene, pick_frames, reference_similarity
from .layout import compose, contact_strip
from .spec import FORMATS, FORMATS_BY_NAME, Brief, Format, Style


@dataclass
class PackResult:
    brief: Brief
    creatives: dict[str, Path] = field(default_factory=dict)
    frame_of: dict[str, int] = field(default_factory=dict)
    clip_frames: list[Path] = field(default_factory=list)
    #: GIF is retained as a lightweight preview for READMEs and browsers.
    clip: Path | None = None
    audio: Path | None = None
    #: H.264/AAC is the actual ready-to-post deliverable.
    video: Path | None = None
    video_error: str | None = None
    reference_consistency: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    backend: str = ""
    meta: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"{len(self.creatives)} creative(s) via {self.backend}"]
        for name, path in self.creatives.items():
            lines.append(f"  {name:7s} {path}  (frame {self.frame_of.get(name, '?')})")
        if self.clip:
            lines.append(f"  preview {self.clip} ({len(self.clip_frames)} frames)")
        if self.audio:
            lines.append(f"  audio   {self.audio}")
        if self.video:
            lines.append(f"  video   {self.video} (H.264/AAC, ready to post)")
        elif self.video_error:
            lines.append(f"  video   unavailable: {self.video_error}")
        for warning in self.warnings:
            lines.append(f"  warning {warning}")
        if self.meta.get("seconds"):
            lines.append(f"  gpu     {self.meta['seconds']}s on {self.backend}")
        return "\n".join(lines)


def make_plate(cut: Image.Image, style: Style, size: tuple[int, int]) -> Image.Image:
    """What the model is shown: the product, cleanly placed on the style wash.

    The plate is deliberately plain. Handing the model a finished-looking
    composition makes it redecorate; handing it the product on a flat ground
    leaves it the job of lighting a scene around something it must not change.
    """
    canvas = Image.new("RGB", size, style.backdrop)
    fitted = _fit(cut, size, band=0.0, fill=0.72)
    canvas.paste(fitted, (0, 0), fitted)
    return canvas


def build_pack(
    cfg: Config,
    backend: Backend,
    photo: Image.Image,
    brief: Brief,
    formats: tuple[Format, ...] = FORMATS,
    contact: str = "",
    write_clip: bool = True,
    on_step=None,
) -> PackResult:
    style = brief.styled()
    photo = ImageOps.exif_transpose(photo)
    out_dir = cfg.out_dir / brief.output_name
    out_dir.mkdir(parents=True, exist_ok=True)
    result = PackResult(brief=brief, backend=backend.name)

    def step(msg: str) -> None:
        if on_step:
            on_step(msg)

    step("cutout")
    cut = backend.remove_background(photo)

    step(f"plate {cfg.width}x{cfg.height}")
    plate = make_plate(cut, style, cfg.size)
    plate.save(out_dir / f"{brief.style}_plate.png")

    step(f"render {cfg.frames} frames on {backend.name}")
    clip: Clip = backend.render(plate, style, cfg)
    result.meta = clip.meta

    return _finish(cfg, brief, style, contact, out_dir, clip, formats,
                   backend.name, write_clip, step, reference=plate)


def build_catalogue(
    cfg: Config,
    backend: Backend,
    jobs: list[tuple[Path, Brief, str]],
    formats: tuple[Format, ...] = FORMATS,
    write_clip: bool = True,
    on_step=None,
) -> list[PackResult]:
    """Pack a whole catalogue against a single pair of model loads.

    Everything a single pack does, done for N products, except that the GPU
    loads its models once for the batch rather than once per photo. On this box
    that saves about 19 seconds of the 60 a small pack takes, so a shop with
    fifty items gets most of a quarter-hour back.
    """
    def step(msg: str) -> None:
        if on_step:
            on_step(msg)

    output_names = [brief.output_name for _, brief, _ in jobs]
    duplicates = sorted({name for name in output_names if output_names.count(name) > 1})
    if duplicates:
        raise ValueError(
            "product names resolve to the same output folder: " + ", ".join(duplicates)
        )

    prepared = []
    for photo_path, brief, contact in jobs:
        style = brief.styled()
        step(f"cutout {brief.product}")
        cut = backend.remove_background(ImageOps.exif_transpose(Image.open(photo_path)))
        plate = make_plate(cut, style, cfg.size)
        key = brief.output_name
        out_dir = cfg.out_dir / key
        out_dir.mkdir(parents=True, exist_ok=True)
        plate.save(out_dir / f"{brief.style}_plate.png")
        prepared.append((key, plate, style, brief, contact, out_dir))

    step(f"render {len(prepared)} product(s) in one batch on {backend.name}")
    clips = backend.render_batch([(k, p, st) for k, p, st, _, _, _ in prepared], cfg)

    results = []
    for key, plate, style, brief, contact, out_dir in prepared:
        clip = clips[key]
        results.append(_finish(cfg, brief, style, contact, out_dir, clip, formats,
                               backend.name, write_clip, step, reference=plate))
    return results


def write_catalogue_audit(
    path: Path,
    photos: list[Path],
    results: list[PackResult],
    *,
    error: str | None = None,
) -> Path:
    """Write an honest batch outcome summary, including warnings and failures."""
    completed = len(results)
    attempted = len(photos)
    packs = []
    for result in results:
        weakest = min(result.reference_consistency.values(), default=0.0)
        packs.append(
            {
                "product": result.brief.product,
                "output_name": result.brief.output_name,
                "status": "review" if result.warnings else "completed",
                "minimum_reference_consistency": round(weakest, 4),
                "warnings": result.warnings,
                "video": str(result.video) if result.video else None,
                "video_error": result.video_error,
            }
        )

    payload = {
        "protocol": "dukaan-catalogue-audit-v1",
        "inputs": [photo.name for photo in photos],
        "attempted": attempted,
        "completed": completed,
        "failed": max(attempted - completed, 0),
        "review_required": sum(pack["status"] == "review" for pack in packs),
        "error": error,
        "packs": packs,
        "note": (
            "Reference consistency is a screening heuristic, not proof of product "
            "identity. Inspect every product before publishing."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def _finish(
    cfg,
    brief,
    style,
    contact,
    out_dir,
    clip,
    formats,
    backend_name,
    write_clip,
    step,
    reference,
):
    """Everything after the GPU: pick stills, compose type, write the pack."""
    result = PackResult(brief=brief, backend=backend_name, meta=clip.meta)
    picks = pick_frames(clip.frames, len(formats), reference=reference)
    for fmt, idx in zip(formats, picks):
        step(f"compose {brief.product} {fmt.name}")
        canvas = compose(fit_scene(clip.frames[idx], fmt.size), brief, fmt, style)
        canvas = contact_strip(canvas, contact, style)
        path = out_dir / f"{brief.style}_{fmt.name}.png"
        canvas.save(path)
        result.creatives[fmt.name] = path
        result.frame_of[fmt.name] = idx
        consistency = round(reference_similarity(reference, clip.frames[idx]), 4)
        result.reference_consistency[fmt.name] = consistency
        if consistency < cfg.consistency_warn:
            result.warnings.append(
                f"review {fmt.name}: reference consistency {consistency:.2f} "
                f"is below {cfg.consistency_warn:.2f}"
            )

    if write_clip:
        frame_dir = out_dir / f"{brief.style}_clip_frames"
        frame_dir.mkdir(exist_ok=True)
        for i, frame in enumerate(clip.frames):
            fp = frame_dir / f"{i:03d}.png"
            frame.save(fp)
            result.clip_frames.append(fp)
        result.clip = _write_gif(clip.frames, out_dir / f"{brief.style}_clip.gif", cfg.fps)
        if clip.audio:
            result.audio = out_dir / f"{brief.style}_clip.wav"
            result.audio.write_bytes(clip.audio)
        try:
            result.video = _write_mp4(
                frame_dir,
                out_dir / f"{brief.style}_clip.mp4",
                cfg.fps,
                result.audio,
            )
        except RuntimeError as exc:
            # The still pack is still useful, but the missing ready-to-post
            # video is made explicit in both the terminal and the manifest.
            result.video_error = str(exc)

    (out_dir / f"{brief.style}_manifest.json").write_text(
        json.dumps(
            {
                "product": brief.product,
                "headline": brief.headline,
                "subline": brief.subline,
                "style": brief.style,
                "contact": contact,
                "backend": backend_name,
                "generation": {"width": cfg.width, "height": cfg.height,
                               "frames": cfg.frames, "fps": cfg.fps, "seed": cfg.seed,
                               "strength": cfg.strength, "refine": cfg.refine},
                "run": clip.meta,
                "creatives": {k: str(v) for k, v in result.creatives.items()},
                "still_from_frame": result.frame_of,
                "reference_consistency": result.reference_consistency,
                "consistency_warning_below": cfg.consistency_warn,
                "warnings": result.warnings,
                "preview_gif": str(result.clip) if result.clip else None,
                "audio": str(result.audio) if result.audio else None,
                "video": str(result.video) if result.video else None,
                "video_error": result.video_error,
            },
            indent=2,
        )
    )
    return result


def relabel(
    pack_dir: Path,
    headline: str | None = None,
    subline: str | None = None,
    contact: str | None = None,
    on_step=None,
) -> PackResult:
    """Change the words on an existing pack without touching the GPU.

    Prices move, offers end, a phone number changes. None of that is a reason
    to regenerate a scene that was already right, and on a metered GPU it is
    the difference between a free edit and another minute of credits. The pack
    keeps its clip frames and records which frame each format was lifted from,
    so the exact same still can be recomposed with new type.
    """
    manifests = sorted(pack_dir.glob("*_manifest.json"))
    if not manifests:
        raise FileNotFoundError(
            f"no pack in {pack_dir}. Relabelling reuses a previous run's frames, "
            "so run `dukaan pack` there first."
        )
    man = json.loads(manifests[0].read_text())
    style_name = man["style"]
    frame_dir = pack_dir / f"{style_name}_clip_frames"
    if not frame_dir.is_dir():
        raise FileNotFoundError(
            f"{frame_dir} is missing, so there are no frames to recompose. The pack was "
            "probably built with --no-clip; rerun `dukaan pack` without it."
        )

    brief = Brief(
        product=man["product"],
        headline=headline if headline is not None else man["headline"],
        subline=subline if subline is not None else man.get("subline", ""),
        style=style_name,
    )
    style = brief.styled()
    contact = contact if contact is not None else man.get("contact", "")
    result = PackResult(brief=brief, backend=man.get("backend", "?"))

    for name, idx in man["still_from_frame"].items():
        fmt = FORMATS_BY_NAME.get(name)
        if fmt is None:
            continue
        src = frame_dir / f"{int(idx):03d}.png"
        if not src.exists():
            raise FileNotFoundError(f"frame {idx} for {name} is missing at {src}")
        if on_step:
            on_step(f"recompose {name} from frame {idx}")
        canvas = compose(fit_scene(Image.open(src).convert("RGB"), fmt.size), brief, fmt, style)
        canvas = contact_strip(canvas, contact, style)
        path = pack_dir / f"{style_name}_{name}.png"
        canvas.save(path)
        result.creatives[name] = path
        result.frame_of[name] = int(idx)

    man.update({
        "headline": brief.headline,
        "subline": brief.subline,
        "contact": contact,
        "relabelled": True,
    })
    manifests[0].write_text(json.dumps(man, indent=2))
    return result


def _write_gif(frames: list[Image.Image], path: Path, fps: int) -> Path:
    """A GIF so the clip previews anywhere, including a GitHub README.

    mp4 is nicer and carries the audio, but needs ffmpeg on the box. The frames
    and the wav are written alongside, so a real encode is one command away.
    """
    if not frames:
        raise ValueError("no frames to write")
    head, *rest = [f.convert("P", palette=Image.ADAPTIVE) for f in frames]
    head.save(path, save_all=True, append_images=rest,
              duration=max(int(1000 / max(fps, 1)), 20), loop=0, optimize=True)
    return path


def _write_mp4(frame_dir: Path, path: Path, fps: int, audio: Path | None = None) -> Path:
    """Mux generated frames and audio into a social-platform-ready MP4."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is not installed; install it to create the H.264/AAC MP4"
        )

    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(max(fps, 1)),
        "-i",
        str(frame_dir / "%03d.png"),
    ]
    if audio and audio.exists():
        cmd += ["-i", str(audio), "-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode or not path.exists() or path.stat().st_size == 0:
        detail = (proc.stderr or proc.stdout or "unknown ffmpeg failure").strip()
        raise RuntimeError(f"MP4 encoding failed: {detail[-400:]}")
    return path
