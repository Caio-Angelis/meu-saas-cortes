# Task 6B.3 — Report

**Status:** DONE  
**Commit:** `checklist: item 6B.3 feito`

## What changed

Inserted exact split detection block in `compute_crop_plan` (`focal_crop.py`) after the `max_faces < 2` → static return and immediately before `_speaker_timeline_crop_segments`. When `SMART_CROP_SPLIT_ENABLED` and clip + 2+ faces with horizontal separation > 35% of source width, returns `{"mode": "split", "left": ..., "right": ...}`. Used existing in-function `cv2` import. Did **not** implement 6B.4 renderer.

## Checklist / docs

- 6B.3 marked `[x]`; diary: `2026-07-14 — última: 6B.3 — próxima: 6B.4 — testes: OK`
- `AI_CONTEXT.md` + `.sdd-briefs/progress-ledger.md` updated

## Tests

`pytest` → **157 passed**

## Concerns

Returning `mode: "split"` without 6B.4 means subtitle_burner may not handle it yet (expected per checklist order).
