# Checklist — Melhorias de Eficiência

## Alto impacto, baixo esforço

- [x] **Transcrição paralela (2 workers)** — `GROQ_TRANSCRIBE_MAX_WORKERS` default é `1` (`app/core/config.py:38`). Para vídeo de 10 min com chunks de 42s, são ~14 chamadas sequenciais. Mudar para `2` reduz o tempo de transcrição em ~50%. O `groq_limiter` (default 2) já protege contra 429.
  - **Ação:** `GROQ_TRANSCRIBE_MAX_WORKERS=2` no `.env` ou mudar o default no código.

- [x] **Tradução em batch** — `TRANSLATE_BATCH` default é `0` (`app/core/config.py:42`). Sem batch, cada segmento vira uma requisição HTTP separada ao Google Translate. Um clipe com 20 segmentos = 20 chamadas. Com batch, agrupa em ~3-4 chamadas.
  - **Ação:** `TRANSLATE_BATCH=1` no `.env` ou mudar o default.

- [x] **Legenda TikTok com modelo 8B** — `tiktok_caption.py:403` chama `groq_user_message_text` sem override de `model`, usando `llama-3.3-70b-versatile` (default em `groq_chat.py:45`). O `projeto.md` §4 diz que a legenda de post deveria usar `llama-3.1-8b-instant` — mais rápido e barato para textos curtos.
  - **Ação:** Passar `model="llama-3.1-8b-instant"` na chamada de `groq_user_message_text` em `tiktok_caption.py`.

## Médio impacto

- [x] **Pular MP3 intermediário na transcrição** — `pipeline.py:351` extrai áudio completo do vídeo, depois `_extract_audio_chunk` fatia esse MP3 com `-c:a copy`. Para vídeos longos, o MP3 completo é um passo extra de I/O.
  - **Ação:** Quando `use_chunks` for verdadeiro, fatiar diretamente do vídeo com `-ss -i video -t chunk -vn` (pular `extract_audio`). Testar compatibilidade de codec.

- [x] **Cache de tradução por texto, não por clip_index** — `cache_pipeline.py:81` inclui `clip_index` na chave. Segmentos repetidos entre clipes são retraduzidos e guardados em disco duas vezes. O `lru_cache(4096)` em `translator.py:27` já evita chamadas HTTP repetidas em memória, mas o cache persistente é redundante.
  - **Ação:** Considerar chave baseada em `(video_fp, target, segment_text_hash)` em vez de `clip_index`.

- [x] **Prep pool com 2 workers para multi-vídeo** — `pipeline.py:588` tem `ThreadPoolExecutor(max_workers=1)`. A prep do vídeo N+1 só começa quando a do N termina. Com 2 workers, a prep do N+2 poderia sobrepor com clipes do N.
  - **Ação:** Avaliar `max_workers=2` no `prep_pool` para filas com 3+ vídeos. Risco: mais concorrência Groq se não houver cache.

## Baixo impacto

- [x] **Memoizar `resolve_ytdlp_cmd()`** — `ytdlp_download.py:74` executa `subprocess.run([... "--version"])` toda vez que `download_video` é chamado. Com 3 URLs em paralelo, são 3 subprocess de validação redundantes.
  - **Ação:** Adicionar `@lru_cache(maxsize=1)` em `resolve_ytdlp_cmd()` (retornar `tuple[str, ...] | None` para ser hashable).

- [x] **Pool compartilhado para captions TikTok** — `pipeline.py:233` cria um `ThreadPoolExecutor(max_workers=1)` por clipe só para a legenda TikTok. Com 5 clipes, são 5 pools criados/destruídos.
  - **Ação:** Substituir por `threading.Thread` (mais leve) ou um pool compartilhado no `_run_clip_stage`.

- [x] **Pré-computar `starts`/`ends` em `_segments_for_clip`** — `pipeline.py:108-109` reconstrói listas `starts` e `ends` a cada chamada. Para N clipes, são N reconstruções sobre todos os segmentos.
  - **Ação:** Pré-computar uma vez em `_run_clip_stage` e passar como parâmetro.

- [x] **Download workers configurável** — `web/worker.py:50` tem `max_workers = max(1, min(3, n_u))` com teto hardcoded em 3.
  - **Ação:** Tornar configurável via env (ex.: `DOWNLOAD_MAX_WORKERS=3`).

## Resumo de prioridade

| # | Melhoria | Esforço | Impacto |
|---|----------|---------|---------|
| 1 | Transcrição paralela (2 workers) | 1 linha .env | Alto |
| 2 | Tradução em batch | 1 linha .env | Médio |
| 3 | Caption com modelo 8B | 1 linha código | Médio |
| 4 | Pular MP3 intermediário | ~20 linhas | Médio |
| 5 | Cache de tradução por texto | ~30 linhas | Médio |
| 6 | Prep pool com 2 workers | 1 linha | Médio |
| 7 | Memoizar `resolve_ytdlp_cmd` | 2 linhas | Baixo |
| 8 | Pool compartilhado para captions | ~10 linhas | Baixo |
| 9 | Pré-computar `starts`/`ends` | ~10 linhas | Baixo |
| 10 | Download workers configurável | ~5 linhas | Baixo |
