# Task 6B.4 / 6B.5 — Split stacked renderer

**Date:** 2026-07-15  
**Status:** IMPLEMENTED

## Summary

Implemented FFmpeg stacked split (top/bottom bands) when crop plan is `mode: "split"`, enabled the flag by default, and added unit tests.

## Changes

- `subtitle_burner.py`: `_build_split_vstack_graph` + split branch in `_prepare_scale_crop_overlay_vf` (returns 5-tuple with `is_filter_complex`); `burn_subtitles` / `cut_and_burn_subtitles` use `-filter_complex` + `-map "[vout]"`; cut path injects `vf_cut` via `[vpre]` and appends `VISUAL_*` before `[vout]`. Split encode always CPU (no VA-API/hwupload).
- `focal_crop.py`: split plan now includes `src_w` / `src_h` for crop clamping.
- `config.py`: `SMART_CROP_SPLIT_ENABLED` default `"1"`.
- `gui.py`: GUI melhorias default `True`.
- `.env.example`: documented split flag + CPU note.
- `tests/test_subtitle_burner_split.py`: 5 unit tests (vstack / static / dynamic).

## Wiring

```
[0:v] → (cut: noise/eq/setpts → [vpre]) → crop+scale L [top] / R [bot]
→ vstack [vsplit] → subtitles+hook+cta(+visual) [vout]
→ -map "[vout]" -map "0:a?"
```

## Tests

- Split unit tests: 5 passed.
- Full suite: **161 passed** with `--deselect=…test_resolve_hit_sfx_prefers_ball_mp3` (pre-existing: missing `assets/ball.mp3`).

## Residual risks

- No visual E2E on a real 2-person far-apart clip in this task (unit-mocked plan).
- Local `.env` with `SMART_CROP_SPLIT_ENABLED=0` still overrides default until removed.
- Cached crop plans without `src_w`/`src_h` fall back to ffprobe; if probe fails, falls back to static cover crop.
