# Progress ledger — checklist-melhorias

Branch: `checklist-melhorias`
Worktree: `/home/caio/Área de trabalho/ProjetosPessoais/meu_saas_cortes/.worktrees/checklist-melhorias`
Model: cursor-grok-4.5-high (subagents)

## Baseline

- Worktree from `a6564b0` (chore: ignore .worktrees)
- Symlinks: `.venv`, `.env` (assets/fonts now real; audio SFX may be local symlinks)
- Pytest atual: **157 passed** (158 baseline −1 teste heurística GPU duplicado na 3.1b)

## Sessão 2026-07-14 — HANDOFF

**Última concluída:** 6B.2  
**Próxima:** **6B.3**  
**Testes:** OK (157)

### Feito nesta sessão (6A + 6B.1–6B.2)

- 6A.1–6A.4: áudio-gate (`_voiced_intervals` / `_is_voiced` + wiring em `compute_crop_plan`)
- 6A.5: verificação visual + pytest (sample 1 falante; sem fonte 2 pessoas disponível)
- 6B.1: `SMART_CROP_SPLIT_ENABLED` em `config.py` (flag only)
- 6B.2: `_two_people_centers` em `focal_crop.py` (ainda não wired)

### Notas

- Fase 6B é opcional/avançada; próximo: **6B.3** (ou pular para **7.1**).
- Sample `temp/checklist_1.6_sample_25s.mp4` não permite validar troca de falante (só 1 pessoa).
- `.env` local: `CLIP_ENCODE_PARALLEL_GPU=4` (não commitado).
