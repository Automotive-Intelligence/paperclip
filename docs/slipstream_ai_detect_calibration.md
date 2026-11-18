# Slipstream AI-detection check: calibration report, 2026-11-18

**The flag means "reads machine-made, needs a human edit pass," not "AI, reject."**
It is a soft signal. It never holds a post, and if it cannot run, publishing proceeds.

## Result: not enabled

The calibration did not produce a usable threshold. `ai_detect.enabled` stays
`false` and `threshold` stays unset in `config/slipstream_brands.yaml`.

The target was: at most 1 in 20 human pieces flags. On this test set the detector
read the two groups backwards, so no threshold meets that target and still
catches engine drafts.

| | Pieces | Median score | Lowest | Highest |
|---|---|---|---|---|
| Human set (Calling Digital blog, Dec 2020 to Jun 2022) | 78 | 0.99 | 0.77 | 0.99 |
| Raw engine drafts (AvI + AIPG, Jul to Nov 2026) | 70 | 0.16 | 0.01 | 1.00 |

Score runs from 0 (reads human) to 1 (reads machine-made).

| Threshold | Human pieces flagged (of 78) | Engine drafts flagged (of 70) |
|---|---|---|
| 0.50 | 78 | 9 |
| 0.80 | 76 | 3 |
| 0.90 | 74 | 2 |
| 0.95 | 68 | 1 |
| 0.99 | 19 | 1 |
| 1.00 | 0 | 0 |

- The only threshold that keeps human flags at or under 1 in 20 is 1.00, which
  flags nothing at all: 0 of 78 human, 0 of 70 engine drafts.
- Separation is 0.02 (1.0 is perfect, 0.5 is a coin flip). A random engine draft
  outscored a random human-set piece 2 percent of the time.
- Holdout check (threshold picked on half the human set, tested on the other
  half) gives the same answer both ways: 1.00, nothing flagged.

## What the numbers do and do not show

1. **The engine's drafts mostly read as human to this detector.** 61 of 70 raw
   drafts score under 0.50, and AvI drafts from August on average between 0.06 and 0.11 by month.
   Even with a perfect human set, the check would rarely fire on what the engine
   writes. The two highest scores (0.92 and 1.00) are two of the five July drafts, from
   the engine's first weeks; most later drafts score low. Only one draft in the
   set was written after the writer moved to Claude on 2026-10-26 (score 0.11).
2. **The human set scored as machine-made, and that set's origin is not proven.**
   The posts were published before ChatGPT existed, which is why they were
   treated as human. But they are uniform 1,250 to 1,450 word SEO articles, and
   content tools built on GPT-3 were on sale in 2021. Two readings fit: the
   detector wrongly flags plain SEO prose written by people, or those posts were
   not fully human-written. This test cannot tell which.
3. **The scoring code is not the cause.** The same pieces scored with the exact
   method on the model card give the same answers, the model card's own AI
   example scores 0.90, and the model file matches the published checksum.
4. **Spoken, clearly human text scores human.** Three sales role-play transcripts
   and a call transcript (4 pieces) scored between 0.00 and 0.17 on all but one
   chunk (0.45). That is too few pieces, and speech is not blog prose, so it is not a
   calibration set.

## What it would take to enable

- A human set whose origin is certain: 20 or more pieces of 250+ words that
  Michael wrote himself. Put them as `.txt` files in a `human/` folder and rerun
  `score` and `report`.
- Even then, expect a low catch rate on engine drafts (see point 1). The
  editorial checklist and the fact-check + editor gate stay the real guardrails.

## How it was run

- Model: `desklib/ai-text-detector-v1.01`, local CPU, no API, no key, no cost.
- Chunks: 600 tokens, 50 overlap, post score is the plain average. 245 human
  chunks and 226 engine chunks scored.
- Speed on an M3 Pro laptop: median 4.5 seconds per post, slowest 10.9. Peak
  memory 1.9 GB. Not measured on Railway.
- AI drafts: every post the engine added to the two site repos, read at the
  commit that added it, passed through the same text extraction the engine step
  uses. Human posts: the Calling Digital site export, posts dated before
  2022-11-30, duplicates and anything under 250 words dropped.
- Test set and per-piece scores are local only (not committed):
  `~/.local/share/slipstream-ai-detect/calibration-2026-11-18/`.

```
python3 scripts/slipstream_ai_detect_calibrate.py score  --dir <folder with human/ and ai/>
python3 scripts/slipstream_ai_detect_calibrate.py report --dir <same folder>
```

Needs `pip install -r requirements-ai-detect.txt`.

## If it is ever switched on

- Add `-r requirements-ai-detect.txt` to `requirements.txt` (the live service
  does not have torch today; without it the check reports "did not run" and
  publishing proceeds).
- Set `ai_detect.threshold` and `ai_detect.enabled: true`.
- A run waits on the check for at most `timeout_s` (240 seconds), then moves on.
