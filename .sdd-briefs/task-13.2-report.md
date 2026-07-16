# Task 13.2 — E2E gap closure (2 speakers)

**Status:** DONE (validation only — no code changes)  
**Date:** 2026-07-15  
**Note:** Ran with `SMART_CROP_SPLIT_ENABLED=0` to avoid unfinished 6B.4 split renderer (parallel agent).

## Source video

| Item | Value |
|------|--------|
| Path | `temp/e2e_13_2_src/e2e_13_2_two_speakers_50s.mp4` |
| Origin | Cut from existing `temp/ytdl_29b36b988c46449e.mp4` @ t=50s, duration 50s |
| Resolution | 1920×1080 + AAC |
| Faces | BlazeFace samples: mostly 2 faces across the clip |

Extract command:

```bash
ffmpeg -y -ss 50 -i temp/ytdl_29b36b988c46449e.mp4 -t 50 \
  -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 128k \
  temp/e2e_13_2_src/e2e_13_2_two_speakers_50s.mp4
```

(YouTube/Wikimedia downloads failed in this environment; reused local download.)

## Pipeline command

```bash
SMART_CROP_SPLIT_ENABLED=0 \
VIRAL_CLIPS_COUNT=1 \
CLIP_DURATION=45 \
OUTPUT_DIR=temp/e2e_13_2_out \
TRANSCRIBE_BACKEND=local \
.venv/bin/python main.py "temp/e2e_13_2_src/e2e_13_2_two_speakers_50s.mp4" \
  2>&1 | tee temp/e2e_13_2_src/e2e_13_2_pipeline.log
```

Runtime ≈ 107s. Log: `temp/e2e_13_2_src/e2e_13_2_pipeline.log`.

## Outputs

| Artifact | Path |
|----------|------|
| Clip MP4 | `temp/e2e_13_2_out/1_e2e_13_2_two_speakers_50s.mp4` (1080×1920, ~39.3s, 69M) |
| TikTok caption | `temp/e2e_13_2_out/1_e2e_13_2_two_speakers_50s.txt` |
| Manifest | `temp/e2e_13_2_out/e2e_13_2_two_speakers_50s__run_manifest_20260715_212644.json` |
| Frames | `temp/e2e_13_2_src/frame_{05,15,28}s.png` |
| Crop plan cache | `~/.cache/meu_saas_cortes/crop_plans/b25a3e4c65fec8f76a225f29742b0d60b74ed1d9bf8a2a98f6d0317be8fcd1f5.json` |

## Checklist gains — confirmed vs skipped

| Gain | Status | Evidence |
|------|--------|----------|
| Local transcription (faster-whisper) | **Confirmed** | Log: `Transcrição LOCAL (faster-whisper na GPU)` + `Carregando faster-whisper (large-v3, float16) na GPU…`; 23 segments with word timings |
| Karaoke + outline + font | **Confirmed (defaults)** | `SUBTITLE_KARAOKE` default on; word-level segments present; frames show yellow subtitle pixels bottom; outline/Montserrat already wired (2A–2C); not re-ASS-inspected |
| Smooth crop / 6A speaker focus | **Confirmed** | Crop plan `mode=dynamic` with `x_expr` alternating left (~483–580) ↔ right (~1687–1874); frames at 5s/15s/28s show different speaker focus |
| Visual identity (progress bar / color) | **Confirmed** | Top 8px band 100% yellow (`VISUAL_PROGRESS_BAR` / `VISUAL_PROGRESS_COLOR=yellow`) on extracted frames |
| NVENC encode | **Confirmed** | `ffprobe` stream tag `encoder=Lavc61.19.101 h264_nvenc` (RTX 5060 Ti) |
| TikTok GUI button | **Skipped** | Per task: GUI click out of scope; 12.4 code smoke already done |
| 6B split screen | **Skipped / deferred** | Forced `SMART_CROP_SPLIT_ENABLED=0` |

## Pytest

Not run — zero code changes (validation only).

## Residual gaps

- Split-mode render (6B.4/6B.5) still deferred.
- Karaoke/Montserrat not re-verified via ASS dump this run (relies on defaults + yellow pixels + prior 2C/2B checks).
- Human TikTok Studio click still optional.
