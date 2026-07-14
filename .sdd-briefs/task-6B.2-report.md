# Task 6B.2 — Report

**Status:** DONE  
**Workdir:** `.worktrees/checklist-melhorias`  
**Date:** 2026-07-14

## Approach

Added exact `_two_people_centers` before `compute_crop_plan` in `focal_crop.py`. Kept unused `out_w`/`out_h`/`vfps`. Did not wire into `compute_crop_plan` (6B.3).

## Pytest

`.venv/bin/python -m pytest -q` → **157 passed** (exit 0).

## Checklist / docs

- Marked **6B.2** `[x]`
- Diary: `2026-07-14 — última: 6B.2 — próxima: 6B.3 — testes: OK`
- `AI_CONTEXT.md` + progress ledger updated

## Commit

`checklist: item 6B.2 feito`

## Concerns

Helper unused until 6B.3; no dedicated unit test for `_two_people_centers`.
