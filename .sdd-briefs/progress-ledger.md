# Progress ledger — checklist-melhorias

Branch: `checklist-melhorias`
Worktree: `/home/caio/Área de trabalho/ProjetosPessoais/meu_saas_cortes/.worktrees/checklist-melhorias`
Model: cursor-grok-4.5-high (subagents)

## Baseline

- Worktree from `a6564b0` (chore: ignore .worktrees)
- Symlinks: `.venv`, `.env` (assets/fonts now real; audio SFX may be local symlinks)
- Pytest atual: **161 passed** (+5 split; 1 env fail `ball.mp3` ausente se não deselect)

## Sessão 2026-07-15 — 6B.4/6B.5

**Última concluída:** 6B.4 + 6B.5 (split renderer)  
**Próxima:** (coord)  
**Testes:** OK (161 passed; deselect SFX sem asset)

### Feito

- Split `filter_complex` (`vstack`) em `subtitle_burner`; flag default `"1"`; GUI `True`
- Unit tests `tests/test_subtitle_burner_split.py`
- Docs: CHECKLIST, AI_CONTEXT, este ledger, `task-6B.4-report.md`

## Sessão 2026-07-14 — HANDOFF (histórico)

**Última concluída:** 7.1  
**Próxima:** **7.2**  
**Testes:** OK (157)

### Feito nesta sessão (6A + 6B + 7.1)

- 6A.1–6A.4: áudio-gate (`_voiced_intervals` / `_is_voiced` + wiring em `compute_crop_plan`)
- 6A.5: verificação visual + pytest (sample 1 falante; sem fonte 2 pessoas disponível)
- 6B.1: `SMART_CROP_SPLIT_ENABLED` em `config.py` (flag only)
- 6B.2: `_two_people_centers` em `focal_crop.py`
- 6B.3: `compute_crop_plan` pode retornar `mode: "split"` (ainda sem renderer)
- 6B.4/6B.5 **ADIADO** (depois implementado em 2026-07-15): ver acima
- **7.1**: `VISUAL_GRADE` / `VISUAL_PROGRESS_BAR` / `VISUAL_PROGRESS_COLOR` / `VISUAL_WATERMARK_TEXT` em `config.py` (flags só; overlay em 7.2)

### Notas

- Sample `temp/checklist_1.6_sample_25s.mp4` não permite validar troca de falante (só 1 pessoa).
- `.env` local pode ainda ter `SMART_CROP_SPLIT_ENABLED=0` (não commitado) — remover para usar o default on.
