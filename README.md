# Dukaan

One product photo in, a shop's worth of ready-to-post creatives out. The
generation runs on an AMD Radeon GPU through ROCm.

A small seller photographs a thing on a counter with a phone. Dukaan cuts the
product out, hands it to LTX-2.3 on the Radeon as a plain plate, gets back a
short lit clip with sound, and lifts a still out of that clip for each format
the seller actually posts: a square for the feed, a 9:16 for stories, a wide
banner for a shop header. The seller's words are never generated, so the price
and phone number are exactly what was typed. Generated frames are ranked against
the input plate for reference consistency and the seller can inspect or replace
the chosen moment before posting. That screening is deliberately not described
as a guarantee that a generative model preserved every product detail.

The GPU runs **once per pack**, not once per format.

![square](docs/gallery/brass-ewer-square.png)

One photo, four looks, with the plated reference beside the generated results:

![four styles](docs/gallery/one-photo-four-styles.png)

Every product under every look, twelve packs, one GPU pass each:

![style matrix](docs/gallery/style-matrix.png)

The model can alter a product. Dukaan therefore shows the plate beside selected
frames in the gallery, ranks candidates with a reference-consistency heuristic,
and requires a human check before publishing.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -e .
```

Without a GPU it runs against a CPU mock, which exercises the whole pipeline
and writes real files, so you can see the layout before spending anything:

```bash
.venv/bin/dukaan pack examples/brass-ewer.png --headline "Handmade brass ewer" --subline "1,450 rupees, free delivery in the city" --style festive --contact "+91 90000 00000"
```

For real generation, point it at the Radeon instance's JupyterLab URL, push the
runner once, and check what you are about to use:

```bash
export DUKAAN_INSTANCE=https://<host>/instances/<instance-id>
.venv/bin/dukaan deploy
.venv/bin/dukaan doctor
```

`doctor` on the box this was built against:

```
video_encoder:   ffmpeg
arch: gfx1100
vram_gb: 48.0
compute_units: 48
torch: 2.10.0+rocm7.2.4.git3d3aa833
card_model: 0x744b
container_ram_cap_gb: 55.0
runner: yes
ready
```

Then the same `pack` command generates for real. One pack is about 75 seconds of
GPU time and a little over two minutes wall clock, including the transfers.

```
3 creative(s) via radeon-ltx
  square  out/silver-bracelet/studio_square.png  (frame 13)
  story   out/silver-bracelet/studio_story.png   (frame 21)
  banner  out/silver-bracelet/studio_banner.png  (frame 48)
  preview out/silver-bracelet/studio_clip.gif (49 frames)
  audio   out/silver-bracelet/studio_clip.wav
  video   out/silver-bracelet/studio_clip.mp4 (H.264/AAC, ready to post)
  gpu     72.6s on radeon-ltx
```

### A catalogue, not a photo

A shop has stock, not a picture. Loading the checkpoint costs about 17 seconds
and the text encoder about 9, every single time, so packing a folder one photo
at a time pays that toll per item for nothing:

```bash
.venv/bin/dukaan catalogue ~/photos --style studio --contact "+91 90000 00000"
```

```
3 pack(s) via radeon-ltx
  brass-ewer       3 creatives   stills 1, 32, 48    45.8s
  gilt-bangles     3 creatives   stills 1, 30, 33    34.6s
  silver-bracelet  3 creatives   stills 13, 21, 48   23.2s
one model load of 17.9s shared across 3 product(s)
audit out/catalogue-audit.json
```

The command also prints each pack's minimum reference-consistency signal. The
audit records attempted, completed, failed and review-required counts. No new
consistency numbers are claimed until the repaired protocol is rerun on Radeon.

Measured with `dukaan bench`: three products cost **134.3 s** batched against
**212.1 s** run separately. The 13.8 s model load is paid once and per-product
time then falls as the GPU warms, for identical work.

### Changing the words costs nothing

Prices move and offers end. The scene was already right, so `relabel`
recomposes the same stills with new type and never touches the GPU:

```bash
.venv/bin/dukaan relabel out/brass-ewer --subline "Diwali price, 1,199 rupees"
```

`dukaan styles` lists the four looks and the three formats. `dukaan bench`
re-measures the table below on your own hardware.

## The interesting part: the instance cannot run its own template

The Radeon instance ships a ComfyUI template for LTX-2.3. It does not work, and
it takes the whole instance down when you try.

| | |
|---|---|
| LTX-2.3 diffusion checkpoint | 43 GB |
| Gemma 3 12B text encoder | 23 GB |
| **Total to load** | **66 GB** |
| Container memory cap (`/sys/fs/cgroup/memory.max`) | **55 GB** |

ComfyUI's server holds every model a graph touches for the life of the process,
so loading both trips the cap. The platform restarts the container mid-prompt:
JupyterLab comes back with zero kernels, the port stops answering, and anything
written outside the persistent volume is gone. It looks like a network fault. It
is not.

`scripts/radeon_ltx.py` runs the same graph in two processes that never overlap:

```
phase 1   load the text encoder, encode the prompts, write the conditioning
          to disk, exit so the process gives its RAM back
phase 2   load the diffusion checkpoint, read the conditioning back, sample,
          write frames and audio, exit
```

Peak becomes `max(43, 23)` instead of `43 + 23`. Measured on the box:

| phase | peak container RAM | wall |
|---|---|---|
| encode, text encoder only | 35.4 GB | 33 s |
| sample, checkpoint only | 49.9 GB | 55 s |
| *stock template, one process* | *trips 55 GB, container restarts* | *n/a* |

Peak is sampled every 200 ms during the run. Reading it at the end instead
reports about 12 GB, because the models have already been evicted by then, and
that is the number a naive benchmark prints.

Three smaller things were needed to make it hold:

- **Conditioning is written in bf16.** It is read back while the 43 GB
  checkpoint is already resident, so every megabyte comes straight off the
  headroom: 1470 MB to 735 MB. It fell to under one megabyte once the right
  encoder was in use, which is also how the wrong one was caught.
- **Models are evicted from VRAM before the VAE decode.** ComfyUI's executor
  does this between nodes; calling node classes directly skips it, and the
  decode died with 2 GB free out of 48.
- **Everything runs under `torch.inference_mode()`.** The LTX VAE updates the
  sampler's output in place, which torch refuses across that boundary.

Two things about the shipped template are worth reporting upstream: its own
notebook cell references `start_comfyui_with_rc_tunnel.sh` while the file on
disk is `start_comfyui_with_tunnel.sh`, so Run All fails; and ComfyUI runs from
`/opt/venv`, not the system python, which is why starting it by hand gives
`No module named 'sqlalchemy'`.

## How a pack is built

```
photo -> cutout -> plate -> ONE GPU pass -> frames + audio
                                         |
                                         +-> pick a still per format
                                         |     -> compose type -> creatives
                                         +-> MP4 with audio
                                         +-> GIF preview + WAV evidence
```

**The cutout fits the background instead of sampling it.** Backdrops are rarely
one colour: a counter shot shades off as window light falls away, and catalogue
photography grades the sweep on purpose. Differencing against a single sampled
colour leaves whichever half of the backdrop drifted furthest. Dukaan fits a
quadratic surface per channel to the border ring and differences against that,
then refits once with the worst quarter of residuals dropped so a product
running off the edge of the frame cannot drag the surface towards itself. A
plane was tried first and left an elliptical pool of backdrop exactly where the
product sits.

**The model is shown a plain plate.** Handing it a finished-looking composition
makes it redecorate. Handing it the product on a flat wash leaves it the job of
lighting a scene around something it must not change.

**Stills are picked for spread, reference consistency and sharpness.** Frames
are split into as many windows as there are formats. Within each window,
structural and colour similarity to the input plate receives most of the score,
with sharpness breaking weak choices. Frame 0 is never eligible: it is the
plate the model was handed, so it shows none of what the model did.

**Reshaping extends by reflection.** A square frame becoming a 9:16 story cannot
be cover-cropped, because the product lives in the sides that would be cut. The
frame is scaled to fit whole and the leftover bands are filled from its own
content, mirrored. Stretching the edge row was tried first and drew the bokeh
background into long horizontal streaks.

**Type is composited, never generated.** Image models garble text, and an early
run put a craft channel's watermark across the bottom of a frame, so the
negative prompt now names the shapes it reaches for. A seller's price and phone
number are the two things on the creative that have to be exactly right.

## Layout

```
dukaan/
  cli.py        pack / styles / doctor / deploy
  config.py     everything from the environment
  instance.py   files, shell and a kernel over the JupyterLab API
  backend.py    the cutout, the mock, and the Radeon backend
  frames.py     still selection and reshaping
  layout.py     type over the picture
  pack.py       the pipeline
  spec.py       formats and styles
scripts/
  radeon_ltx.py the two-phase runner, runs on the instance
  radeon.py     put / get / run against the instance from a shell
```

There is no ssh to the box, only a JupyterLab URL, so `instance.py` uses its
REST API as the transport: contents for files, kernels for a process, a
websocket for stdout. Long jobs are started with `setsid` so a culled kernel
cannot take them down, and progress is read back from their logs. Frames come
home as one tar rather than fifty base64 requests, which halved wall time and
stopped the tunnel resetting mid-pack.

## Product-consistency boundary

LTX is a generative model, so no prompt can guarantee that a logo, stone or
engraving remained exact. Dukaan now gives every selected frame a lightweight
`reference_consistency` signal based primarily on central structure, records it
in the manifest, warns below `DUKAAN_CONSISTENCY_WARN`, and lets the seller move
to any generated frame in the browser without another GPU call. The signal is a
screening and ranking heuristic, not an identity metric.

The current gallery uses credited CC0 catalogue photographs. Run `dukaan
catalogue` on a folder of ordinary phone photographs to produce
`catalogue-audit.json`, then publish successes, warnings and failures together.
Plain or graded backgrounds are supported; clutter, transparent products and
severe reflections remain documented limitations of the deterministic CPU
cutout.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The automated suite runs with no GPU required. It covers the cases that were
actually wrong at some point: phone EXIF orientation, safe output paths, a
graded backdrop, a product touching the frame edge, reference-aware still
selection, MP4 muxing, catalogue audits and reflection versus streaking when a
frame is reshaped.

## Licence

Apache-2.0. The demo photographs are not mine and are listed with their sources
in [examples/CREDITS.md](examples/CREDITS.md); all four are CC0.
