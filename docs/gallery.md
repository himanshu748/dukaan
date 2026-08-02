# Gallery

Every image below came out of one `dukaan pack` run against a Radeon PRO
(gfx1100, 48 GB) through ROCm, generated at **1536x1536** with the refine
pass on, so the stills are downscaled into their formats rather than
stretched up. Each row is a single GPU pass: one clip, and
three stills lifted from three different moments of it.

The input photographs are CC0 museum object shots, credited in
[../examples/CREDITS.md](../examples/CREDITS.md). LTX is generative and can alter
the product even when prompted not to. The comparisons below are visual
evidence, not a guarantee; the type is composited afterwards.

---

## Every product, every style

Twelve packs: each product run under each look. One photograph in four styles
shows the styles work; this shows they work across the catalogue rather than on
one lucky input.

![style matrix](gallery/style-matrix.png)

Look at the bangles on the festive ground: the marigolds show *through* the ring
interiors. That is the cutout correctly treating an enclosed pocket of backdrop
as backdrop, which an earlier version got wrong by filling it and turning two
rings into two discs.

**The styles do not all relight to the same degree, and the grid shows it.**
Studio, festive and midnight keep the flat catalogue view they were given.
Daylight, whose plate backdrop sits furthest from a plain sweep, sometimes
re-stages instead: the silver bracelet comes back standing on the wooden surface
with real depth rather than lying flat. The dragon heads, engraving, open gap
and border pattern appear consistent in this example, but the model has inferred
volume that the flat input did not contain. A seller should still compare it to
the plate before choosing that style for a product whose exact shape matters.

The blue-and-white porcelain vase is deliberately absent. Its photograph carries
a vignette behind the object that the backdrop fit cannot see, and the patch it
leaves is fused to the vase, so it comes out with a streak beside it. Several
ways of removing it were measured and none paid for itself; the numbers are in
[profile.md](profile.md), section 4.2. Showing it here as though it were good
output would be the dishonest choice.

---

## Reference and selected-frame comparison

`strength=0.7` on the image-to-video conditioning can reduce drift but cannot
prevent it. Each row is one historical pack: the plate the model was handed,
then the frames the older selection protocol chose.

The corrosion marks on the bangles, engraving and pitting on the ewer, and
dragon heads on the bracelet appear consistent in these examples while the
scene changes. These rows are useful visual evidence, but they are not a
measured field success rate.

The repaired pipeline now records a zoom-tolerant reference-consistency
heuristic for screening and ranking. It is deliberately not called an identity
score. Pixel comparisons remain misleading when the motion prompt changes scale
and position, so final acceptance still belongs to the seller.

---

## One photo, four looks

The same photograph under every style Dukaan ships. Only the style name changed
between these four runs. The plate is provided for comparison because a seller
cannot post a picture of a thing they will not ship.

![four styles](gallery/one-photo-four-styles.png)

---

## Handmade brass ewer, festive

Plate the model was given, then the three creatives.

<img src="gallery/brass-ewer-plate.png" width="260">

| square, frame 16 | story, frame 20 | banner, frame 48 |
|---|---|---|
| <img src="gallery/brass-ewer-square.png" width="240"> | <img src="gallery/brass-ewer-story.png" width="135"> | <img src="gallery/brass-ewer-banner.png" width="300"> |

![clip](gallery/brass-ewer-clip.gif)

65.3 s of GPU time. The banner is the case that drove the reflection fix:
stretching the edge column instead drew the bokeh into horizontal streaks.

---

## Hand-worked silver bangle, studio

<img src="gallery/silver-bracelet-plate.png" width="260">

| square, frame 13 | story, frame 21 | banner, frame 48 |
|---|---|---|
| <img src="gallery/silver-bracelet-square.png" width="240"> | <img src="gallery/silver-bracelet-story.png" width="135"> | <img src="gallery/silver-bracelet-banner.png" width="300"> |

![clip](gallery/silver-bracelet-clip.gif)

72.6 s of GPU time. The engraving appears consistent in this selected example;
the repaired pipeline additionally flags low-consistency choices for review.

---

## What the numbers were

| product | style | GPU | wall clock | frames | stills from |
|---|---|---|---|---|---|
| brass ewer | festive | 65.3 s | 2 m 20 s | 49 | 16, 20, 48 |
| silver bangle | studio | 72.6 s | 2 m 19 s | 49 | 13, 21, 48 |

Wall clock includes the plate upload, both model loads and pulling 49 frames
plus a wav back over the tunnel. Before frames were fetched as one tar it was
3 m 34 s.
