# AI_CONTEXT — meu_saas_cortes

Documento de contexto para outra IA ou desenvolvedor entender o projeto **sem ler o código inteiro**. Última revisão: **2026-07-13**, alinhada ao código em `app/`, `main.py`, `gui.py` e `web_main.py`.

**Especificação de produto (fonte de verdade):** `projeto.md` — comportamento desejado, regras de negócio e fluxos; implementações novas devem seguir esse arquivo. **Otimização de eficiência (checklist validado):** `checklist.md` — passos aprovados após revisão de risco ao fluxo (env, código e itens excluídos). **Expansão (quiz):** `projeto.md` §13; backend em `app/quiz_pipeline.py` — `run_quiz_pipeline` (Etapas 1–4). **Batalha 1v1:** `app/batalha_pipeline.py` — `run_batalha_pipeline_from_payload` (Groq → imagens → TTS gancho + `script_narracao` → Pymunk → FFmpeg stdin + SFX). **História (vídeo narrado):** `app/historia_pipeline.py` — `run_historia_pipeline` (Groq cenas → TTS + ComfyUI por cena → FFmpeg loop/sync + concat → `OUTPUT_DIR/historias/historia_final_<timestamp>.mp4`). **GUI:** `gui.py` com `ttk.Notebook` — abas «Cortes Virais», «Máquina de Quizzes», «Batalha 1v1», «História» e «Text-to-Speech»; log/resultados globais; worker roteia `job_type` (`cortes` → `run_pipeline`, `quiz` → `run_quiz_pipeline`, `batalha` → `run_batalha_pipeline_from_payload`, `historia` → `run_historia_pipeline`, `tts` → `app/tts_standalone.synthesize_tts_mp3`). **Telegram:** `telegram_bot.py` — bot local (`python-telegram-bot` v21+, asyncio); só responde a `TELEGRAM_ALLOWED_USER_ID`; `/quiz` → `run_quiz_pipeline`, `/cortes` → yt-dlp + `run_pipeline`; envia cada MP4 com legenda `.txt` recomendada. Este `AI_CONTEXT.md` é mapa técnico complementar.

Os arquivos `README.md` e `FLUXO_DE_DADOS.md` descrevem o fluxo básico (o README ainda menciona `groq==0.11.0` e o padrão antigo `*_viral_N.mp4` — **versão Groq e nomes de saída reais estão abaixo**); este arquivo inclui recursos adicionais (smart crop 9:16 com falante dinâmico, cache, GPU VA-API, dublagem Edge-TTS, legendas TikTok, GUI com barra de progresso, yt-dlp).

**Python no Linux (PEP 668):** o `pip` do sistema costuma recusar instalação global; use **`python3 -m venv .venv`** na raiz e **`.venv/bin/pip install -r requirements.txt`**. `main.py` e `gui.py` chamam `_venv_reexec.ensure_venv(__file__)`: se `.venv` existir e o processo não estiver nela, reinicia com `.venv/bin/python`.

**Desktop Linux:** logo após o `ensure_venv`, `main.py` e `gui.py` chamam `app/linux_desktop_bootstrap.apply_linux_desktop_defaults()` — define `TF_CPP_MIN_LOG_LEVEL`, `GLOG_minloglevel` e limita threads de OpenBLAS/MKL/OMP para reduzir ruído no stderr (TensorFlow Lite / absl) e oversubscription ao rodar vários FFmpeg em paralelo. Em setups **AMD híbridos** (ex.: Ryzen 5600G + RX 5500 XT), também define `DRI_PRIME=1` (Mesa tende a preferir a GPU dedicada). Com **NVIDIA dedicada** (ex.: RTX 5060 Ti), `DRI_PRIME` **não** é definido.

**Groq + httpx:** o projeto fixa **`groq>=1.2.0`** (SDK atual compatível com `httpx` 0.28+). Versões antigas (`groq==0.11.0`) quebram com `TypeError: ... unexpected keyword argument 'proxies'`.

**Verificação após alterações:** com o venv ativo e dependências de dev instaladas (`pip install -r requirements-dev.txt`), rode **`pytest`** na raiz (config em `[tool.pytest.ini_options]` no `pyproject.toml`, `testpaths = ["tests"]`, `addopts = "-q"`). Em jul/2026 a suíte tem **157 testes** (worktree `checklist-melhorias`; +2 em `test_subtitle_burner_karaoke.py` no item **2C.4**) e cobre lógica pura e integrações leves **sem** chamar Groq, FFmpeg ou yt-dlp na maior parte dos casos — ideal para checar regressões rápidas antes de um processamento completo.

**Checklist melhorias (worktree `checklist-melhorias`):** itens **1.1–1.6** — `faster-whisper==1.2.1` no `.venv`; config `TRANSCRIBE_BACKEND` / `LOCAL_WHISPER_*`; `local_whisper.py` + atalho em `transcriber.py` (`TRANSCRIBE_BACKEND=local`, fallback Groq); `.env.example` documentado. **1.6** E2E OK: log `Transcrição LOCAL (faster-whisper na GPU)` com `temp/checklist_1.6_sample_25s.mp4` → `resultados/1_checklist_1.6_sample_25s.mp4`; pytest OK. **2A.1** — estilo ASS TikTok em `ass_builder.write_tiktok_ass_from_srt`: negrito + BorderStyle contorno (sem caixa opaca) + outline 4 + sombra 1. **2A.2** — verificação visual OK (`temp/check_2A2_frame.png`: amarelo bold + contorno preto, sem caixa retangular); pytest OK. **2B.1** — `assets/fonts/Montserrat-Bold.ttf` (SIL OFL, ~445KB) empacotado. **2B.2** — `TIKTOK_SUBTITLE_FONT` / `FONTS_DIR` em `config.py`. **2B.3** — `fontsdir` no filtro `subtitles` em `_prepare_scale_crop_overlay_vf` (`subtitle_burner.py`). **2B.4** — override de `fonte` `None`/`""`/`"Arial"` → `TIKTOK_SUBTITLE_FONT` no início de `_prepare_scale_crop_overlay_vf`. **2B.5** — verificação visual OK (`temp/check_2B5_frame.png`: Montserrat Bold geométrica, não Arial); pytest OK. **2C.1** — `SUBTITLE_KARAOKE` / `SUBTITLE_KARAOKE_HIGHLIGHT` em `config.py` (default ligado, highlight `#FFE000`). **2C.2** — `write_tiktok_ass_karaoke_from_srt` + `_hex_to_ass` em `ass_builder.py` (tags `{\k}` por palavra). **2C.3** — import de `write_tiktok_ass_karaoke_from_srt` em `subtitle_burner.py`. **2C.4** — branch em `_prepare_scale_crop_overlay_vf`: se `SUBTITLE_KARAOKE` → karaoke ASS, senão plain. **2C.5** — verificação visual OK (`temp/check_2C5_{a,b,c}.png`: branco→amarelo avança na mesma linha); pytest OK. **3.1** — `_clip_uses_gpu_encoder` retorna `USE_GPU_CLIP_ENCODE` (todos os clipes na GPU quando ligado). **3.1b** — `test_gpu_on_all_clips_use_gpu` substitui os testes da regra antiga; `test_gpu_off_never` mantido; pytest verde. **3.2** — NVENC preset `p6`/`cq 19` + bf/lookahead em `gpu_clip_encoder_ffmpeg_args()`. **3.3** — `.env` `CLIP_ENCODE_PARALLEL_GPU=4` (RTX 16 GB); `.env.example` comentário alinhado. **3.4** — E2E multi-clipe NVENC OK: `VIRAL_CLIPS_COUNT=3` em `ytdl_514bda5a94f24646.mp4` → 5 MP4 em `resultados/` com tag `encoder=… h264_nvenc`; log `CPU/GPU conforme configuração`; pytest OK. **4A.1** — import de `gpu_clip_encoder_ffmpeg_args` em `batalha_ffmpeg.py`. **4A.2** — `encode_simulation_to_silent_mp4` usa `*gpu_clip_encoder_ffmpeg_args()` no `cmd` (NVENC com fallback libx264). `requirements.txt` **não** foi alterado.

---

## 1. O que é o projeto

**Nome conceitual:** “SaaS de Cortes Virais” (na prática é um **pipeline local em Python**, não um serviço web hospedado).

**Entrada:** um ou mais arquivos de vídeo longos (MP4 etc.) e, opcionalmente, **URLs** (YouTube etc.) pela GUI.

**Saída:** para cada vídeo, **N clipes curtos** (padrão **5** clipes de **~50 s** cada, configurável), em formato **vertical 9:16** (1080×1920 por padrão), com:
- legendas **queimadas** (hardcoded) no idioma escolhido (`pt` ou `en`);
- texto de **gancho** (“hook”) no topo nos primeiros ~3 s (vindo do modelo de viralidade);
- **CTA** “siga o perfil” entre ~13 s e ~15 s;
- opcionalmente **dublagem** (Edge-TTS) substituindo o áudio;
- um arquivo **`.txt`** ao lado de cada MP4 com **legenda de postagem** estilo TikTok (descrição + hashtags), gerada por LLM; se o vídeo veio de **URL (yt-dlp)**, acrescenta linha de crédito ao canal (ex. `Review original: Peewee`) e URL do canal quando disponível.

O pipeline acelera levemente o vídeo (`CLIP_SPEED_UP_PERCENT`, padrão 2%) e aplica pequenos filtros no corte (ruído/brilho) para reduzir “cara de reupload”.

---

## 2. Stack e dependências de sistema

| Camada | Tecnologia |
|--------|------------|
| Linguagem | Python 3.10+ (alvo de lint `py312` no `pyproject.toml`) |
| Vídeo/áudio | **FFmpeg** + **ffprobe** (obrigatório; `config` resolve caminho e verifica filtro `drawtext`) |
| IA — transcrição | **Groq** API Whisper (default) ou **faster-whisper local** (`TRANSCRIBE_BACKEND=local`); áudio longo no caminho Groq é fatiado e remontado |
| IA — momentos virais + legenda de post + hooks | **Groq** chat, default `llama-3.3-70b-versatile`; **legenda TikTok** (texto do post) usa o mesmo modelo, prompt ancorado na transcrição do clipe + hook on-screen; hashtags de conteúdo (filtra genéricas tipo #plotwist); fallback extrai palavras da transcrição |
| Tradução | **Google Translate** via `deep-translator` (com modo batch opcional) |
| TTS (padrão GUI / História / Quiz / Batalha) | **Kokoro** local GPU (`app/local_tts.py`) quando instalado; senão **Gemini** ou **edge-tts** |
| TTS dublagem (cortes) | **edge-tts** (ainda cloud; voz local nas outras abas via `tts_engine`) |
| Smart crop | **OpenCV** + **MediaPipe Tasks** (Face Detector / BlazeFace `blaze_face_full_range.tflite`, baixado na primeira execução; hash opcional `BLAZEFACE_TFLITE_SHA256`); com **2+ rostos**, estima falante por movimento na boca e pode gerar crop **dinâmico** (`x`/`y` em função de `t` no FFmpeg); abertura do vídeo com **backend FFmpeg** (`CAP_FFMPEG`) e fallback para o padrão |
| Download por URL | **yt-dlp** (`yt-dlp[default]` em `requirements.txt`; cookies opcionais por env) |
| GUI | **tkinter** (stdlib) + **`sv-ttk`** (tema Sun Valley escuro; fallback `clam` escuro se o pacote não estiver instalado) |

Arquivos de dependências:
- `requirements.txt` — runtime principal (ex.: `mediapipe==0.10.35`, `yt-dlp[default]==2026.3.17`, `opencv-python-headless==4.10.0.84`, **`sv-ttk>=2.0.0`**, **`groq>=1.2.0`**, `edge-tts==7.2.8`, **`pymunk>=6.8.0`**, **`duckduckgo-search>=7.0.0`** — Pymunk reservado para Batalha 1v1 Fase 2+).
- `requirements-extra.txt` — opcional fora do núcleo (`yt-dlp[default]`, `secretstorage` para cookies do navegador no Linux).
- `requirements-local-tts.txt` — **Kokoro** TTS local (pt-BR); instale PyTorch CUDA antes — ver `scripts/install_local_tts.sh` (RTX 50xx / Blackwell precisa **cu128**).
- `requirements-dev.txt` — `pytest>=8`, `ruff>=0.6`.
- `pyproject.toml` — `[tool.ruff]` e `[tool.pytest.ini_options]` apenas (sem empacotamento Poetry/setuptools).

---

## 3. Como executar

### CLI (`main.py`)

```bash
python main.py <video1> [video2 ...] [--lang pt|en] [--position bottom|top] [--font ...] [--color #RRGGBB] [--bg-color #RRGGBB] [--opacity 0-100] [--dub-en | --dub-pt] [--tts-voice NOME_VOZ]
```

- **Bootstrap Linux:** igual à GUI — `apply_linux_desktop_defaults()` após o `ensure_venv`.
- Aceita **vários vídeos**; com lista, a **preparação** (extração de áudio + transcrição + momentos virais) do **próximo** arquivo pode rodar em **paralelo** enquanto os **clipes** do vídeo atual ainda estão sendo codificados.
- Vídeos com mesmo nome base recebem sufixo `__2`, `__3` no nome de saída para evitar colisão.

### GUI (`gui.py`)

```bash
python gui.py
# recomendado em distro com PEP 668:
.venv/bin/python gui.py
```

- **Bootstrap Linux:** antes de importar o pipeline, `apply_linux_desktop_defaults()` (ver cabeçalho deste doc).
- **Logging:** `setup_logging(gui_quiet=True)` no arranque — nível padrão WARNING no terminal e filtro de loggers ruidosos (`absl`, `mediapipe`, `tensorflow`). Durante o worker, `gui_pipeline_log_redirect()` envia logs do app para a mesma fila do `print` (painel de log), com formato compacto.
- **Entrada:** arquivo(s) local(is) e/ou **URLs** (uma por linha, placeholder cinza até foco). Ordem: locais primeiro; depois downloads **com até 3 URLs em paralelo** (`ThreadPoolExecutor`, ordem final preservada). Botão **[?]** com ajuda sobre cookies/403 do YouTube.
- **Tema:** escuro — **Sun Valley `dark`** (`sv_ttk.set_theme("dark")`) ou, sem `sv-ttk`, tema **clam** escuro customizado; UI em **seções** (`_make_section` com ícones Unicode); faixa superior ciano; `Panedwindow` vertical entre resultados e log; **barra de progresso** verde (0–100%) alimentada por `run_pipeline(..., progress=...)`.
- **Execução / ao concluir:** mesmas opções da CLI (idioma, legendas, dublagem, voz TTS); checkboxes para **abrir pasta `OUTPUT_DIR`**, **notificação** (`notify-send` / macOS) e **exportar `.zip`** ao terminar. Mensagens curtas em português no log (`_pipeline_log_line`: download, transcrição, clipes). **Erros:** resumo `[ERRO]` + traceback em vermelho no painel de log; `messagebox.showerror` ao terminar com falha.
- **Pós-processo:** tabela da última execução (nome + duração via **ffprobe** em thread); botões **copiar legenda** (`.txt` TikTok), **copiar caminho** do MP4, **copiar todas as legendas**, **exportar .zip** (`app/gui_export.py` — inclui MP4, `.txt` ao lado e `LEIA-ME_POSTAGEM.txt`).
- **Aba Text-to-Speech:** área de texto + combobox de voz (**Kokoro local GPU** quando instalado, senão **Gemini** ou **Edge**); padrão **★ Dora** (`local:pf_dora`); **«Ouvir amostra»** / **«Gerar MP3»** via `app/tts_standalone.py` + `tts_engine`.
- **Aba História:** área de texto da história + voz TTS; **«Gerar vídeo da história»** chama `run_historia_pipeline` (Groq → cenas → TTS + ComfyUI → FFmpeg); saída em `resultados/historias/`; requer ComfyUI em `127.0.0.1:8188`.
- **Worker → UI:** fila `queue.Queue` com tuplas especiais — `("__PROGRESS__", frac)` atualiza a barra; `("__DONE__", lista_de_saídas, had_error)` antes do sentinela `None`; `_handle_pipeline_done` preenche a tabela, põe progresso em 100% e dispara pasta/notificação/zip conforme flags. Controles desabilitados enquanto `_pipeline_running`.
- **Cancelar** chama `request_cancel()` — FFmpeg via `run_cancelable` termina de forma cooperativa.
- Log no widget `tk.Text` truncado (~500 linhas); stdout/stderr do worker redirecionados para a fila.

### Bot Telegram (`telegram_bot.py`)

```bash
python telegram_bot.py
# ou: .venv/bin/python telegram_bot.py
```

- **Env:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID` em `.env` / `app.config` (ver `.env.example`).
- **Biblioteca:** `python-telegram-bot>=21` (API asyncio v20+).
- **Comandos:** `/start`, `/help`; `/quiz <tema> [quantidade] [timer_sec]`; `/cortes` com URL(s) ou caminho(s) local(is) na mesma mensagem (um por linha) — download via `yt-dlp` quando houver links.
- **Segurança:** qualquer outro `user.id` recebe bloqueio; mensagens de texto não-comando também.
- **Pipeline:** `asyncio.to_thread` — quiz → `run_quiz_pipeline`; cortes → download (como GUI) + `run_pipeline` (defaults PT, legendas padrão); `asyncio.Lock` impede dois jobs simultâneos.
- **Saída:** cada MP4 com `send_video` e legenda do `.txt` ao lado (`Path(mp4).with_suffix('.txt')`); arquivos >50 MB: caminho local + legenda em mensagem separada; `global_error_handler` evita crash do processo.
- **Progresso no chat:** durante quiz/cortes, a mensagem de status é editada a cada ~2 s com linhas da `log_queue` do pipeline; heartbeat ~75 s se não houver log novo. Quiz no bot força `EDGE_TTS_MAX_CONCURRENT=1` (menos 403 no Edge-TTS). TTS do quiz usa `edge_tts_save_to_path` direto (sem loop duplo de retry).

### Testes (`pytest`)

```bash
pip install -r requirements-dev.txt   # se ainda não instalou
pytest                                 # ou: python -m pytest
```

Use após alterações no código para validar regressões nas partes cobertas (ver mapa em §6 `tests/`).

### Venv automático

`_venv_reexec.ensure_venv(__file__)` em `main.py` e `gui.py`: se existir `.venv/` na raiz e o `sys.prefix` ativo não for essa venv, o processo **reinicia** com o Python do `.venv` (não instala pacotes sozinho — crie a venv e rode `pip install -r requirements.txt` uma vez).

---

## 4. Variáveis de ambiente (visão geral)

Carregadas com `python-dotenv` em `app/config.py` (e `load_dotenv()` na importação).

| Área | Variáveis importantes |
|------|------------------------|
| Obrigatória (IA) | `GROQ_API_KEY` |
| Pastas | `OUTPUT_DIR`, `TEMP_DIR`, `CACHE_DIR` (cache persistente; padrão `~/.cache/meu_saas_cortes` no Linux) |
| Clipes | `CLIP_DURATION`, `VIRAL_CLIPS_COUNT`, `CLIP_SPEED_UP_PERCENT` |
| Saída vertical | `OUTPUT_VIDEO_WIDTH`, `OUTPUT_VIDEO_HEIGHT`, `TIKTOK_SUBTITLE_*` (margem rodapé efetiva ≈ `TIKTOK_SUBTITLE_MARGIN_V` × 2.0 × 1.55 em `subtitle_burner.py`, para ficar acima da UI do TikTok) |
| Groq transcrição | `GROQ_TRANSCRIBE_CHUNK_SEC`, `GROQ_TRANSCRIBE_SINGLE_MAX_SEC`, `GROQ_TRANSCRIBE_MAX_WORKERS` (default **1** — menos 429 ao fatiar) |
| Groq HTTP / concorrência | `GROQ_HTTP_TIMEOUT_SEC` (default **180** s em `groq_chat` / `transcriber`); `GROQ_MAX_IN_FLIGHT` (default **2**), `GROQ_RETRY_*` (`app/limits.py`) |
| Tradução | `TRANSLATE_BATCH`, `TRANSLATE_BATCH_MAX_CHARS`, `TRANSLATE_MAX_IN_FLIGHT`, `TRANSLATE_RETRY_*` |
| Smart crop | `SMART_CROP_ENABLED`, `SMART_CROP_FRAME_SAMPLES`, `SMART_CROP_SPEAKER_FPS`, `SMART_CROP_MIN_CHANGE_INTERVAL_SEC`, `SMART_CROP_MEDIAPIPE_GPU`, `SMART_CROP_MEDIAPIPE_GPU_FORCE`, `BLAZEFACE_TFLITE_SHA256` (opcional) |
| Bootstrap Linux (sem env obrigatório) | `app/linux_desktop_bootstrap.py`: `DRI_PRIME` só sem driver NVIDIA; `TF_CPP_MIN_LOG_LEVEL`, `GLOG_minloglevel`, `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS` com `setdefault` no início de `main.py` / `gui.py` |
| Encode paralelo | `CLIP_ENCODE_PARALLEL_CPU`, `CLIP_ENCODE_PARALLEL_GPU` (padrão **3** com NVIDIA no Linux; RTX 16 GB: use **4** no `.env` — item 3.3), `USE_GPU_CLIP_ENCODE`, `CLIP_GPU_ENCODER` (vaapi/amf/nvenc/qsv), **`VAAPI_RENDER_NODE`**; no **Linux**, se `CLIP_GPU_ENCODER` não estiver definido, `config` escolhe **`h264_nvenc`** quando há driver NVIDIA + encoder no FFmpeg (ex.: RTX 5060 Ti), senão **`h264_vaapi`** (Mesa/AMD), senão **`h264_amf`**. Sem **`VAAPI_RENDER_NODE`** no `.env`, no Linux o padrão é o nó **`renderD*`** de **maior índice** se existirem **dois ou mais** (heurística AMD híbrido). Com **VA-API**, o comando inclui `-init_hw_device`/`-filter_hw_device` e sufixo `format=nv12,hwupload=…` na cadeia de vídeo (`subtitle_burner`, `video_cutter`, `tts_dubber`). NVENC usa preset `p6`, `tune hq`, `cq 19`, `-bf 3`, `-b_ref_mode middle`, `-rc-lookahead 20`. `PIPELINE_MAX_WORKERS` ou heurística `PIPELINE_CPU_FRACTION` / `PIPELINE_CPU_PER_CLIP_ESTIMATE`; **`-threads` por processo** em encodes CPU (`clip_ffmpeg_threads_args`) |
| Dublagem / Edge-TTS | `EDGE_TTS_VOICE`, `EDGE_TTS_VOICE_PT`, `EDGE_TTS_REQUEST_TIMEOUT_SEC`, `EDGE_TTS_MAX_CONCURRENT`, `EDGE_TTS_RETRIES`, `DUB_TRIM_SILENCE`, `DUB_SILENCE_*`, `DUB_MAX_TTS_SPEEDUP` |
| TTS local (Kokoro) | `LOCAL_TTS_DEVICE`, `LOCAL_TTS_VOICE_PT`, `LOCAL_TTS_SPEED`, `LOCAL_TTS_PREFERRED` |
| Pipeline workers | `PIPELINE_MAX_WORKERS`, `PIPELINE_CPU_FRACTION`, `PIPELINE_CPU_PER_CLIP_ESTIMATE` |
| yt-dlp | `YTDLP_PATH`, `YT_DLP_PATH`, `YTDLP_COOKIES_FROM_BROWSER`, `YTDLP_COOKIES_FILE` |
| Legenda — crédito ao canal (download URL) | `CAPTION_SOURCE_ATTRIBUTION` (default ligado), `CAPTION_SOURCE_LINE_PT`, `CAPTION_SOURCE_LINE_EN` (`{channel}` no template) |
| Logging | `LOG_LEVEL` |

Detalhes e comentários adicionais: `.env.example`.

---

## 5. Fluxo do pipeline (ordem real)

Função principal: `app/pipeline.py` → `run_pipeline(..., progress=None)` → `_prepare_transcription_and_moments()` + `_run_clip_stage()` por vídeo (com sobreposição na fila multi-arquivo). O callback `progress` recebe **0.0–1.0** por vídeo (multi-arquivo: média `(índice + t) / total`). Marcos aproximados: **0.02** início prep → **0.05** extração áudio → **0.12** transcrição → **0.48** pós-transcrição → **0.5–0.58** momentos virais → **0.58–1.0** estágio de clipes (atualizado conforme clipes terminam).

1. **Diretórios** — garante `OUTPUT_DIR` e `TEMP_DIR`.
2. **Fingerprint do vídeo** — `app/cache.py` → `fingerprint_file()` (tamanho, mtime; hash head/tail ou arquivo inteiro se pequeno).
3. **Segmentos (transcrição)** — tenta `load_cached_segments()`; se não houver cache:
   - `extract_audio()` (`audio_extractor.py`) → MP3 em `temp/`;
   - `transcribe_audio()` (`transcriber.py`) → lista `{start, end, text}` via Groq Whisper; áudio temporário removido;
   - `save_cached_segments()`.
4. **Momentos virais** — tenta `load_cached_moments()`; se não:
   - `analyze_viral_moments()` (`viral_analyzer.py`): monta texto da transcrição com limite de caracteres, prompt em inglês para o modelo, espera JSON array com `start`, `end`, `reason`, `hook`; pós-processamento (`_refine_clip_window`, remoção de sobreposição, normalização do hook);
   - `save_cached_moments()`.
5. **Clipes em paralelo** — `ThreadPoolExecutor` com até `pipeline_thread_pool_max_workers()` tarefas; **semáforos** limitam quantos encodes **GPU** vs **CPU** rodam ao mesmo tempo (`BoundedSemaphore` alinhado a `CLIP_ENCODE_PARALLEL_*`). Cada tarefa é `_process_clip_task()`:
   - **`_segments_for_clip`**: segmentos que intersectam a janela do clipe (busca de janela com `bisect` em starts/ends); recorte proporcional de texto nas bordas.
   - **Tradução** para `--lang`: cache por clipe; `GoogleTranslator` reutilizado por `(source, target)` via `lru_cache` de módulo.
   - **`cut_and_burn_subtitles()`** (`subtitle_burner.py`): **único passe** FFmpeg no vídeo fonte — `-ss`/`-t`, filtros de corte+velocidade, escala/crop 9:16, ASS, `drawtext` hook/CTA; encoder **CPU** ou **GPU** com fallback. `video_cutter.cut_video()` permanece para uso pontual, mas o pipeline principal não gera mais MP4 intermediário só de corte.
   - **`generate_srt()`** + legenda TikTok: **`generate_tiktok_post_caption()`** dispara em **paralelo** (thread pool interno por clipe) com o encode, pois não depende do MP4 final.
   - **Smart crop**: `compute_crop_plan(..., clip_start=, clip_end=)` amostra só o trecho no arquivo fonte; chave de cache de crop inclui `clip_start`/`clip_end` arredondados.
   - **Dublagem** (se `dub_to`): Edge-TTS em **paralelo** por clipe (`asyncio.gather`); encaixe FFmpeg + `mux_video_with_new_audio` (**`-c:a copy`** quando ambos AAC); opcional `remove_long_silence_from_video()` com encoder **GPU** e fallback CPU.
   - Remove temporários (`srt`, intermediários de dublagem).
6. **Manifesto de execução** — JSON em `OUTPUT_DIR` com nome `*__run_manifest_*.json` (caminho, fingerprint, opções, cache hits, momentos, outputs).

---

## 6. Mapa de arquivos (código-fonte relevante)

### Raiz

| Arquivo | Função |
|---------|--------|
| `main.py` | CLI: `argparse`, chama `apply_linux_desktop_defaults()`, `setup_logging()` e `run_pipeline()`; flags `--dub-en`, `--dub-pt`, `--tts-voice`. |
| `gui.py` | Classe `CortesApp`: `ttk.Notebook` (Cortes Virais + Máquina de Quizzes + Batalha 1v1 + História + TTS); log e tabela de resultados **globais** na base; fila com `job_type` (`cortes` / `quiz` / `batalha` / `historia` / `tts`); tema `sv-ttk`; aba quiz: **cor de fundo** (`cor_fundo`, padrão `#1A1A1A`) + paleta; aba batalha: tema, modo (tamanho/território/plinko), voz TTS do gancho; aba história: texto longo + voz TTS → `run_historia_pipeline` (ComfyUI local); `apply_linux_desktop_defaults()`; `setup_logging(gui_quiet=True)`. |
| `web_main.py` / `web_worker.py` | FastAPI local (`app/web/`) e worker **RQ** (Redis). Ver §9. |
| `app/web/` | `store.py` (SQLite playlist), `queue_backend.py`, `tasks.py`, `worker.py`, `hub.py` (SSE), routers `jobs` / `playlist` / `progress` / `runs`, templates + `static/js/app.js`. |
| `app/gui_export.py` | `ffprobe_duration_seconds`, `format_duration_hms`, `desktop_notify`, `export_cortes_zip` (usado pela GUI). |
| `app/clip_output_naming.py` | `sanitize_clip_output_stem` — caracteres seguros no stem do arquivo de saída. |
| `_venv_reexec.py` | Reexecução automática com Python de `.venv`. |
| `.env` / `.env.example` | Segredos e tuning (não versionar `.env` com chaves reais). |
| `requirements*.txt` | Dependências. |
| `README.md` | Visão geral e estrutura (parcialmente simplificada). |
| `FLUXO_DE_DADOS.md` | Fluxo em etapas (não lista smart crop, GPU, dublagem, cache). |
| `baixarlinks.bat` / `baixarlinks.sh` | Scripts auxiliares de download em lote (fora do núcleo Python; opcional). |
| `app/linux_desktop_bootstrap.py` | `apply_linux_desktop_defaults()` — só Linux; `DRI_PRIME` só em AMD híbrido; variáveis TF/absl + threads de BLAS. |

### `app/config.py`

Única fonte de constantes de ambiente: Groq, diretórios, duração/contagem de clipes, resolução 9:16, smart crop, encoders GPU, pipeline workers, Edge-TTS, dublagem, resolução FFmpeg/ffprobe. **`_resolve_ffmpeg()`** é memoizado (`lru_cache`); **`clip_ffmpeg_threads_args()`** limita `-threads` por processo em encodes CPU. No Linux, **`linux_has_nvidia_driver()`** e **`_linux_h264_hw_encoders_in_ffmpeg()`** (memoizado) leem driver/FFmpeg para escolher o padrão de **`CLIP_GPU_ENCODER`** (NVIDIA → `h264_nvenc`; AMD → `h264_vaapi` antes de `h264_amf`). **`VAAPI_RENDER_NODE`** (padrão Linux: **`_default_vaapi_render_node_linux()`** — com 2+ nós `renderD*`, o de maior índice, típico AMD híbrido), **`clip_gpu_uses_vaapi()`**, **`ffmpeg_vaapi_hwdevice_args()`**, **`ffmpeg_vaapi_vf_hwupload_suffix()`** suportam encode VA-API (Mesa + AMD).

### `app/pipeline.py`

Orquestração completa: `run_pipeline(..., progress=, source_by_path=)` — mapa caminho absoluto → `VideoSourceAttribution` (GUI/web após yt-dlp); preparação (`_prepare_transcription_and_moments` com marcos 0.02–0.58), estágio de clipes (`_run_clip_stage` 0.58–1.0), sobreposição preparação↔encode na fila multi-vídeo, semáforos CPU/GPU, manifesto, dublagem + caption TikTok com crédito ao canal.

### `app/quiz_pipeline.py`

Máquina de Quizzes (§13): `run_quiz_pipeline` (payload aceita `cor_fundo` / `bg_color` / `quiz_bg_color` → `normalize_quiz_bg_color`) → Etapa 1 LLM (`generate_quiz_questions_llm` com `difficulty` + **verificação factual** `verify_quiz_questions_llm` — 2ª passagem Groq corrige índice/resposta errada e fatos desatualizados; prompts ancorados no ano civil; temperatura baixa; `generate_quiz_opening_llm` — gancho intro) → `generate_quiz_audio_async` + TTS do gancho (Edge-TTS: **só pergunta** na fase 1; **resposta correta + curiosidade** na fase 3; timer **tick/s** via FFmpeg) → `generate_quiz_frames` / `app/quiz_frames.py` (Pillow: fundo configurável, padrão `#1A1A1A`; `#N`, frame gancho estilo cartão «QUIZ» + subtítulo, micro-recompensa, outro) → `assemble_quiz_video_ffmpeg` (ordem: **gancho** 2,5–3 s → por pergunta loop PNG + `filter_complex` — barra «Pergunta N/Total», countdown central, **flash/shake** na revelação (overlay, sem `crop`+`enable`), `amix` ding opcional, concat n=3 → **micro-recompensa** 1 s entre perguntas → **outro**; concat demuxer `-c copy`; legenda via `tiktok_caption`). Pulse nos botões: **não** usa `drawbox` ciano no FFmpeg (desalinhado dos cantos PIL); destaque no timer = número regressivo. Testes: `tests/test_quiz_pipeline_unit.py`.

### `app/historia_pipeline.py` + `app/comfyui_client.py`

**Pipeline História** — texto longo → vídeo narrado. Máx. **`HISTORIA_MAX_VIDEOS=5`** gerações ComfyUI; Groq devolve ≤5 cenas (excedente fundido em blocos); cenas sem troca de prompt reutilizam o MP4 anterior com **loop FFmpeg** (`-stream_loop -1` + `-shortest`) até a narração da cena acabar; escala **1080×1920** no mux; concat → **`OUTPUT_DIR/historias/historia_final_<timestamp>.mp4`**.

**`app/comfyui_client.py`** — **`gerar_video_comfyui(prompt_visual, output_path)`** (stdlib: `urllib`, `json`, `time`, `random`): carrega **`workflow_historia.json`** (raiz), injeta prompt no nó **4**, seed aleatório nos nós **6** (KSampler) e **13** (SEGSDetailer), latente **512×896** (9:16 nativo SD 1.5, múltiplos de 64) no nó **1** e **`frame_rate=3`** no nó **8**, POST **`http://127.0.0.1:8188/prompt`**, polling **`/history/{prompt_id}`** (~1,75 s), extrai saída do nó **8** (`gifs` ou `images`), download **`/view?filename=…&subfolder=…&type=output`**, grava binário em **`output_path`**. O pipeline escala cada cena para **`OUTPUT_VIDEO_WIDTH×OUTPUT_VIDEO_HEIGHT`** (1080×1920) no mux FFmpeg.

**`workflow_historia.json` (blindagem anti-artefatos):** nó **5** — negative prompt reforçado (qualidade/anatomia/duplicações); nó **6** — `cfg=7.0`, `steps≥20`; nó **1** — `512×896`. **ADetailer (Impact Pack):** após **VAEDecode (7)**, cadeia **UltralyticsDetectorProvider (9)** `bbox/face_yolov8m.pt` → **ImpactSimpleDetectorSEGS_for_AD (10)** → **ToBasicPipe (12)** com prompt facial **«perfect highly detailed face, realistic eyes»** (nó **11**) → **SEGSDetailerForAnimateDiff (13)** → **SEGSPaste (14)** → **VHS_VideoCombine (8)**. Requer **ComfyUI-Impact-Pack** + **ComfyUI-Impact-Subpack** (nó `UltralyticsDetectorProvider`) instalados no ComfyUI local.

### `app/batalha_pipeline.py` + `app/batalha_images.py` + `app/batalha_frames.py` + `app/batalha_ffmpeg.py`

**Batalha 1v1** — vídeos verticais 1080×1920 com duelo por física 2D (TikTok/Shorts). **Fase 1:** `generate_batalha_spec_llm` (Groq) + `prepare_batalha_assets` (`batalha_images.py`: logos via **Wikimedia Commons** (`collect_logo_image_urls`, prioriza PNG com «logo» no título); fallback Wikipedia summary e só então DuckDuckGo; tema `X vs Y` → «X logo»/«Y logo»; `fetch_opponent_graphics` → `avatar_*.png` (circular, bolinhas) + `logo_*.png` (proporção original, tela final); `cleanup_batalha_downloaded_assets` apaga ambos após o MP4). **Fase 2:** `app/batalha_frames.py` — **Pymunk 7** (`Space.on_collision`, `post_solve` → `collision_times_sec` para SFX), `BatalhaSimulationBase`, `draw_textured_ball`, modos **`TamanhoSimulation`**, **`TerritorioSimulation`**, **`PlinkoSimulation`**. **Plinko (Corrida):** pinos + cestos; zona **sem teto** (entrada livre); divisórias físicas finas entre faixas; pontuação por X ao **entrar** na cesta (`plinko_ball_entered_basket_zone`, sem precisar chegar ao chão); física livre após pontuar; congela só no encerramento; 5 bolinhas/time; placar → **~0,45 s** (`PLINKO_POST_SETTLE_SEC`) → tela cheia «Vitória do {filme}» com **logo retangular** do vencedor logo abaixo + TTS (mesma voz do gancho, pré-gerado para ambos oponentes; `ensure_plinko_victory_screen` se timeout); `mux_batalha_video_with_audio` com `adelay` na vitória; SFX de colisão **`assets/ball.mp3`** (timestamps Pymunk `begin` bola×pino/parede/bola; Plinko com debounce curto). Tamanho/Território inalterados. `iter_simulation_frames` / `create_simulation`. **Fase 3:** `app/batalha_ffmpeg.py` — `encode_simulation_to_silent_mp4` (stdin `rawvideo` rgb24), `build_collision_sfx_filter` (`adelay` + `amix`), `mux_batalha_video_with_audio` (3 vozes: **intro** `audio_hook.mp3` → **meio** `audio_narracao.mp3` com `adelay` = duração do gancho via `ffprobe` → **vitória** Plinko opcional; SFX de colisão com volume reduzido para não abafar a narração). **`run_batalha_pipeline_from_payload`:** Groq (`script_narracao` 50–60 palavras) → imagens → TTS → simulação+encode → MP4 em `OUTPUT_DIR` + `.txt` TikTok. **GUI:** aba «Batalha 1v1» (`job_type` `batalha`). Testes: `tests/test_batalha_pipeline_unit.py`, `tests/test_batalha_frames_unit.py`, `tests/test_batalha_ffmpeg_unit.py`.

### `app/local_tts.py` + `app/tts_standalone.py` + `app/tts_engine.py` + `app/gemini_tts.py` + `app/tts_voices.py`

**TTS local (Kokoro-82M):** `local_tts_save_to_path` — modelo `hexgrad/Kokoro-82M` via pacote `kokoro`; **pt-BR** (`lang_code='p'`); vozes `pf_dora`, `pf_sara`, `pm_alex`, `pm_santa`; singleton `KPipeline` em thread; WAV 24 kHz → MP3 via FFmpeg. Instalação: **`bash scripts/install_local_tts.sh`** (PyTorch **cu128** para RTX 50xx / sm_120; ~1 GB VRAM). Env: `LOCAL_TTS_DEVICE` (`auto`|`cuda`|`cpu`), `LOCAL_TTS_VOICE_PT`, `LOCAL_TTS_SPEED`, `LOCAL_TTS_PREFERRED` (padrão 1 — voz local primeiro na GUI).

TTS avulso (GUI aba «Text-to-Speech»): **`tts_engine.synthesize_speech_to_path`** despacha **`local`** (Kokoro), **Gemini** ou **Edge-TTS** conforme `voice_id` (`local:pf_dora`, `gemini:Achernar`, `edge:pt-BR-…`, ou nome Edge legado). Com Kokoro instalado, a GUI lista vozes locais primeiro (padrão **Dora**); senão Gemini com **`GEMINI_API_KEY`**; senão Edge. Gemini: REST + fallback Edge em falha. `synthesize_tts_mp3` → **`OUTPUT_DIR/tts/`**; pré-ouvir em `TEMP_DIR/tts_preview/`. Quiz/Batalha/História usam o mesmo despacho. Testes: `tests/test_tts_standalone_unit.py`, `tests/test_tts_voices_unit.py`.

### `app/cache.py` e `app/cache_pipeline.py`

- `cache_dir()`, `fingerprint_file()` (ver acima), `key_hash()`, `cache_path()`, `read_json` / `write_json` (temporário com **UUID**).
- `_segments_compact` usa `round(..., 3)` nos tempos para chaves de cache de tradução estáveis entre módulos.
- Namespaces de cache: `segments/`, `moments/`, `translations/`, `crop_plans/` (plano de crop pode incluir `clip_start`/`clip_end` no JSON de opções da chave).

### `app/limits.py`

Semáforos `groq_limiter` e `translate_limiter`, políticas de retry (`with_retries`).

### `app/cancel.py` + `app/subprocess_utils.py`

Cancelamento cooperativo; `run_cancelable` usa `wait(timeout)` em loop e drena PIPE ao terminar.

### `app/logging_setup.py`

`setup_logging()` em stdout (`LOG_LEVEL` ou `gui_quiet` → WARNING + filtro `_SuppressNoisyLoggers`). `gui_pipeline_log_redirect(stream)` — context manager usado pela GUI para espelhar logs do app na fila do painel durante o worker.

### `app/ytdlp_download.py`

`resolve_ytdlp_cmd()` valida o executável com `--version` e cai para **`python -m yt_dlp`** se o script `.venv/bin/yt-dlp` tiver shebang quebrado (projeto movido de pasta). Normaliza URLs, cookies via env. `download_video()` devolve **`DownloadResult`** (`path` + `attribution` opcional): usa `--write-info-json`, lê `channel`/`uploader` do JSON e apaga o `.info.json`. Tipos `VideoSourceAttribution`, `lookup_source_attribution()` para o pipeline. Progresso via **logging** (nível INFO).

### `app/ai_integrations/`

| Módulo | Função |
|--------|--------|
| `groq_chat.py` | Chat Groq com limite + retry; **`model`** configurável (default `llama-3.3-70b-versatile`). Cliente **singleton** lazy (`_get_client`). |
| `transcriber.py` | Whisper via Groq (ou **local** se `TRANSCRIBE_BACKEND=local` e faster-whisper disponível — atalho no topo de `transcribe_audio`); cliente `Groq` criado **no import** (`timeout` de `GROQ_HTTP_TIMEOUT_SEC`); fatias respeitam `GROQ_MAX_IN_FLIGHT`; **`-c:a copy`** no MP3 extraído; ffprobe/ffmpeg via `run_cancelable`. |
| `viral_analyzer.py` | Prompt + parse JSON + refinamento de janelas + hooks. |
| `translator.py` | Tradução por segmento ou batch; cache LRU por texto; limiter + retries. |
| `tiktok_caption.py` | Prompt JSON para descrição de post (conteúdo do clipe, sem hashtags genéricas de viral); `llama-3.3-70b-versatile`; fallback com hashtags da transcrição; `append_source_attribution_to_caption`; `save_tiktok_caption_file`. |
| `__init__.py` | Pacote. |

### `app/video_processing/`

| Módulo | Função |
|--------|--------|
| `audio_extractor.py` | Áudio FFmpeg → MP3 temporário. |
| `video_cutter.py` | Corte + filtros + speed-up (uso secundário; pipeline principal usa `cut_and_burn_subtitles`). Prefixo/sufixo VA-API quando encode GPU for `h264_vaapi`. |
| `subtitle_burner.py` | `burn_subtitles` (input já cortado) e **`cut_and_burn_subtitles`** (fonte + intervalo: um encode). Legendas ASS no rodapé com margem ampliada (`SUBTITLE_BOTTOM_MARGIN_MULTIPLIER`, `SUBTITLE_RAISE_FACTOR`) para não cobrir nome do perfil no TikTok. Fallback CPU **sem** hwupload se GPU falhar. |
| `focal_crop.py` | Plano de crop; **`compute_crop_plan(..., clip_start, clip_end)`** amostra só o trecho; delegate MediaPipe **GPU por padrão** com driver NVIDIA no Linux (ou `SMART_CROP_MEDIAPIPE_GPU=1`); CPU em AMD+Mesa salvo `SMART_CROP_MEDIAPIPE_GPU_FORCE=1`; `SMART_CROP_MEDIAPIPE_GPU=0` força CPU; crop dinâmico para falante com 2+ rostos; **`_opencv_file_capture()`** tenta `CAP_FFMPEG` primeiro. |
| `tts_dubber.py` | Edge-TTS com limite `EDGE_TTS_MAX_CONCURRENT`, retries e timeout; mux com **AAC copy** quando possível; remoção de silêncio com GPU opcional (incl. sufixo VA-API no ramo `filter_complex`). |

### `app/subtitle/`

| Módulo | Função |
|--------|--------|
| `formatter.py` | Conversão segundos ↔ timestamp SRT. |
| `srt_generator.py` | Gera SRT; regex de limpeza **pré-compiladas** no módulo. |
| `ass_builder.py` | Gera ASS para TikTok a partir do SRT (PlayRes, margens, cores ASS). Estilo Default: bold + contorno (BorderStyle=1), sem caixa opaca. |

### `tests/`

Testes automatizados com **`pytest`** (`conftest.py` coloca a raiz do repo em `sys.path`).

| Arquivo | Foco |
|---------|------|
| `test_pipeline_segments.py` | `_segments_for_clip` |
| `test_pipeline_segments_extra.py` | bordas vazias / `bisect` |
| `test_pipeline_gpu_heuristic.py` | `_clip_uses_gpu_encoder` (índice **1-based** como no executor) |
| `test_ass_builder.py` | `write_tiktok_ass_from_srt` |
| `test_srt_generator.py` | `generate_srt` (offset, `playback_speed`, filtros) |
| `test_subtitle_formatter.py` | `seconds_to_srt_timestamp` |
| `test_translate_batch.py` | tradução em lote com `translate_text` mockado |
| `test_ytdlp_download.py` | `normalize_media_url`, `collect_urls_from_lines`, `attribution_from_ytdlp_info`, fallback `resolve_ytdlp_cmd` |
| `test_cache.py` | `read_json` / `write_json`, `key_hash`, `fingerprint_file` (incl. arquivo pequeno), `cache_path` + `CACHE_DIR` |
| `test_cache_pipeline.py` | `crop_plan_cache_opts`, roundtrip de cache de segmentos |
| `test_limits.py` | `RetryPolicy`, `with_retries`, `ConcurrencyLimiter` |
| `test_cancel.py` | `request_cancel`, `raise_if_cancelled` |
| `test_viral_analyzer_parse.py` | `_extract_json_array`, `_sanitize_json`, `_parse_moments`, helpers de janela |
| `test_tiktok_caption_unit.py` | `_extract_json_object`, hashtags, fallback, `save_tiktok_caption_file` |
| `test_gui_export.py` | `format_duration_hms`, `export_cortes_zip` |
| `test_pipeline_output_stem.py` | `sanitize_clip_output_stem` / nomes de saída |
| `test_batalha_pipeline_unit.py` | parse/normalize spec, máscara circular, fallback de avatar |
| `test_batalha_frames_unit.py` | simulação curta, frames RGB |
| `test_batalha_ffmpeg_unit.py` | `filter_complex` de colisões + mixagem |
| `test_config_gpu_encoder.py` | resolução automática `CLIP_GPU_ENCODER` (NVIDIA vs AMD) |

**Não coberto pelos testes automatizados:** pipeline completo, FFmpeg, download real com yt-dlp, chamadas à API Groq, smart crop com vídeo real (exigem ambiente e/ou rede).

---

## 7. Contratos de dados importantes

### Segmento de transcrição / tradução

```python
{"start": float, "end": float, "text": str}
```

### Momento viral (após `analyze_viral_moments`)

```python
{"start": float, "end": float, "reason": str, "hook": str}  # hook curto, até ~5 palavras normalizadas
```

### Saída de vídeo

- **Vídeo final** (`app/pipeline.py` → `_process_clip_task`): `{OUTPUT_DIR}/{índice}_{stem_sanitizado}.mp4`  
  - `índice` = **1..N** (um por momento viral, na ordem do modelo).  
  - `stem_sanitizado` = `sanitize_clip_output_stem(video_name)` (`app/clip_output_naming.py`); `video_name` é o stem do arquivo de entrada ou override `stem__k` quando há colisão na fila multi-arquivo.
- **Legenda de post TikTok:** mesmo path do MP4 com extensão **`.txt`** (`save_tiktok_caption_file`).
- **Manifest:** `{OUTPUT_DIR}/{video_name}__run_manifest_{YYYYMMDD_HHMMSS}.json` (`video_name` lógico do pipeline, não o stem do MP4 de saída).

> Legado documental: README/FLUXO antigos citam `*_viral_N.mp4` — isso **não** corresponde mais ao código.

---

## 8. Comportamentos que confundem se não estiverem documentados

1. **Cache persistente** — rerodar o mesmo arquivo (mesmo fingerprint) pode **pular** transcrição e/ou análise de momentos; apagar cache em `CACHE_DIR` ou mudar o arquivo força recomputação.
2. **Momentos dependem de `output_language`** — cache de momentos inclui `target_language`; trocar `--lang` pode refazer só a parte de legendas/tradução conforme chaves de cache de tradução por clipe.
3. **GPU** — com `USE_GPU_CLIP_ENCODE`, **todos** os clipes usam o encoder GPU (`_clip_uses_gpu_encoder` → flag); **semáforos** limitam quantos encodes GPU/CPU rodam ao mesmo tempo (padrão **3** encodes NVENC em paralelo com NVIDIA no Linux; RTX 16 GB use **4** no `.env` — itens **3.3–3.4**); falha de driver cai para CPU nos pontos de encode. No Linux + **NVIDIA**, o padrão é **NVENC** (`h264_nvenc`); no Linux + Mesa/AMD, **VA-API** (`h264_vaapi`) e heurística de `renderD*` para iGPU+dGPU.
4. **Smart crop** — primeira execução pode **baixar** `blaze_face_full_range.tflite`; requer rede; sem rosto confiável cai em centro/movimento; com **NVIDIA** no Linux, delegate GPU do MediaPipe é **ligado por padrão**; em AMD+Mesa usa CPU salvo `SMART_CROP_MEDIAPIPE_GPU_FORCE=1` (ver `.env.example`).
5. **Barra de progresso (GUI)** — frações vêm de marcos fixos no pipeline, não do tempo real de cada FFmpeg; útil como indicador grosseiro, não ETA exato.
6. **Dublagem** — áudio original é substituído; `DUB_TRIM_SILENCE` pode cortar vídeo em silêncios longos (comportamento documentado no `.env.example`); muitas sínteses Edge-TTS em paralelo podem gerar 403 — use `EDGE_TTS_MAX_CONCURRENT`.
7. **Groq rate limits** — `GROQ_TRANSCRIBE_MAX_WORKERS` (default 1) e `GROQ_MAX_IN_FLIGHT` (default 2) devem ser ajustados junto com quota; mensagens de retry aparecem no log.
8. **GUI** — fluxo TikTok continua **manual** no app; a interface só gera `.txt` e atalhos de cópia/exportação (não há upload automático). Downloads por URL incluem **crédito ao canal** no `.txt` quando o yt-dlp expõe metadados; vídeos locais não têm essa linha.

## 9. Interface web local + fila de jobs

| Peça | Caminho / comando |
|------|-------------------|
| Servidor | `web_main.py` → FastAPI em `app/web/` (porta **8765**, `WEB_HOST` / `WEB_PORT`) |
| Worker RQ | `web_worker.py` (requer `REDIS_URL`; fila `RQ_QUEUE_NAME`, default `cortes`) |
| Persistência | SQLite em `data/web_jobs.sqlite` (`app/web/store.py`) |
| Pipeline | `app/web/worker.py` → `run_pipeline(..., progress=)` + `ProgressHub` (SSE `/api/progress`) |
| Sem Redis | jobs em **thread** no processo do `web_main.py` (`app/web/queue_backend.py`) |

**Playlist / workflow:** cada entrada tem `workflow_status` (`pendente` \| `publicado` \| `descartado`) e `pipeline_status` (`idle` \| `queued` \| `running` \| `done` \| `error`). API: `GET/POST /api/playlist`, `POST /api/playlist/process`, `PATCH /api/playlist/{id}` (marcar publicado/descartado). UI: botões *Adicionar à playlist*, *Processar playlist*, *Publicado* / *Descartar* por linha após o pipeline concluir.

**Job avulso:** `POST /api/jobs` (form multipart, igual à GUI) enfileira um item ou lote via `process_playlist_item_task` / `process_playlist_batch_task` (`app/web/tasks.py`).

Dependências web extras em `requirements.txt`: `fastapi`, `uvicorn`, `redis`, `rq`.

---

## 10. O que ainda **não** é

- Não há hospedagem SaaS multi-tenant, autenticação de usuários nem upload automático ao TikTok.
- Celery não está integrado (só **RQ** + fallback em thread).

---

## 11. Extensão natural (para outra IA continuar o trabalho)

- Substituir Edge-TTS ou tradutor por serviços pagos com SLA.
- Adicionar mais idiomas além de `pt`/`en` nos prompts e nas `choices` do argparse/GUI.
- Expandir URLs de playlist YouTube em vários itens (`yt-dlp --flat-playlist`) ao adicionar à fila.

Este arquivo deve ser suficiente para orientar leitura **dirigida** do código (começar por `pipeline.py` e `config.py`) em vez de varredura cega de todo o repositório.
