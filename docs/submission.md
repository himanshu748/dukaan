# AMD DevMaster Hackathon, Track 1

**Project:** Dukaan
**Entrant:** himanshu748
**Hardware:** AMD Radeon PRO, gfx1100 (RDNA 3), 48 GB VRAM, 48 CUs, via ROCm 7.2.4
**Model:** LTX-2.3 22B (audio-video), Gemma 3 12B text encoder

## What it is

One product photo in, a shop's worth of ready-to-post creatives out. A small
seller photographs a thing on a counter. Dukaan cuts the product out, hands it
to LTX-2.3 on the Radeon as a plain plate, gets back a short lit clip with
sound, and lifts a still out of that clip for each format the seller posts:
square for the feed, 9:16 for stories, wide banner for a shop header.

The GPU runs once per pack, not once per format.

## What runs on the AMD GPU

The whole generative step. `scripts/radeon_ltx.py` executes on the instance and
drives LTX-2.3's real audio-video graph: text encode, image-to-video latent
conditioning, a concatenated audio and video denoise, separation, tiled VAE
decode, audio VAE decode. Measured 62 to 73 seconds per pack for 49 frames at
768x768 plus 2 seconds of stereo audio.

Everything cheap and deterministic stays on the CPU on purpose: the cutout, the
still selection, the reshaping and the type. Spending GPU credits on a
background subtraction that a least-squares fit does exactly would be waste.

## The optimization contribution

**The instance cannot run the template it ships with.** The LTX-2.3 checkpoint
is 43 GB and its Gemma text encoder is 23 GB. The container is capped at 55 GB
(`/sys/fs/cgroup/memory.max`). ComfyUI's server holds every model a graph
touches for the life of the process, so loading both trips the cap and the
platform restarts the container mid-prompt. JupyterLab comes back with zero
kernels and the port stops answering, which reads as a network fault.

Dukaan splits the graph into two processes that never overlap, so peak is
`max(43, 23)` rather than `43 + 23`:

| phase | peak container RAM | wall |
|---|---|---|
| encode (text encoder only) | 35.4 GB | 33 s |
| sample (checkpoint only) | 51.2 GB | 55 s |
| stock template, one process | trips the 55 GB cap, container restarts | n/a |

Three further fixes were needed:

1. Conditioning is written in bf16, because it is read back while the 43 GB
   checkpoint is already resident. 1470 MB to 735 MB.
2. Models are evicted from VRAM before the VAE decode. ComfyUI's executor does
   this between nodes; calling node classes directly skips it, and the decode
   died with 2.13 GB free out of 47.98.
3. Everything runs under `torch.inference_mode()`. The LTX VAE updates the
   sampler's output in place, which torch refuses across that boundary.

## Two template bugs worth reporting upstream

- The LTX notebook's own cell calls `start_comfyui_with_rc_tunnel.sh`; the file
  on disk is `start_comfyui_with_tunnel.sh`. Run All fails.
- ComfyUI runs from `/opt/venv`, not the system python, so starting it by hand
  the obvious way gives `No module named 'sqlalchemy'`.

## Reproducing

```bash
python -m venv .venv && .venv/bin/pip install -e .
export DUKAAN_INSTANCE=https://<host>/instances/<instance-id>
.venv/bin/dukaan deploy
.venv/bin/dukaan doctor
.venv/bin/dukaan pack examples/brass-ewer.png --headline "Handmade brass ewer" --subline "1,450 rupees, free delivery in the city" --style festive --contact "+91 90000 00000"
```

Without `DUKAAN_INSTANCE` the same command runs the whole pipeline against a CPU
mock and writes real files, so the layout can be checked before spending
anything.

`.venv/bin/python -m pytest tests/ -q` runs 26 tests with no GPU.

## Results

Three products, three styles, nine creatives and three clips with audio, all
generated on the Radeon: see [gallery.md](gallery.md).

## Licence

Apache-2.0. Demo photographs are CC0, credited in `examples/CREDITS.md`.
