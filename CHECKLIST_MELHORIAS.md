# CHECKLIST DE IMPLEMENTAÇÃO — melhorias do meu_saas_cortes

> Este arquivo transforma o `MELHORIAS.md` em passos executáveis, um a um.
> Ele foi escrito para ser seguido por uma IA de execução, **em vários chats diferentes** (a IA tem contexto limitado). **Este arquivo é a memória do projeto — não a sua.**

---

## 🔁 PROTOCOLO MULTI-CHAT — LEIA ISTO SEMPRE QUE ABRIR UM CHAT NOVO

A implementação vai acontecer em **vários chats**. Cada chat novo **não lembra** do anterior. Por isso, a verdade sobre "o que já foi feito" **não está na sua memória — está neste arquivo (os checkboxes) e no histórico do git.**

### ▶️ Ao COMEÇAR um chat novo, faça nesta ordem:

1. **Leia este arquivo inteiro** (é a sua única fonte de contexto).
2. **Veja o que já foi commitado:**
   ```bash
   git log --oneline -20
   ```
3. **Leia o "DIÁRIO DE BORDO" abaixo** — a última linha diz onde o chat anterior parou.
4. **Ache o primeiro item `- [ ]` (não marcado)** deste checklist, de cima para baixo. **É daí que você continua.**
5. **CONFIRME que o item anterior (`- [x]`) realmente foi aplicado** antes de seguir — abra o arquivo citado e verifique se a mudança está lá. Se um item está marcado mas a mudança **não** está no código, o chat anterior falhou: refaça esse item.
6. Rode os testes para garantir que o projeto está são antes de continuar:
   ```bash
   .venv/bin/python -m pytest -q
   ```

### ✅ Ao TERMINAR cada item:

1. Marque o checkbox: troque `- [ ]` por `- [x]` **neste arquivo**.
2. Faça commit incluindo o número do item na mensagem:
   ```bash
   git add -A && git commit -m "checklist: item X.Y feito"
   ```
   > O commit é o que permite o próximo chat saber o que aconteceu. **Sem commit, o progresso se perde.**

### 🛑 Quando o SEU contexto estiver acabando (handoff):

1. **Termine o item que está fazendo** (não pare no meio de uma edição). Se não der, **reverta** o item incompleto com `git checkout -- <arquivo>` para não deixar o código quebrado.
2. Garanta que `pytest` passa.
3. Marque os checkboxes concluídos.
4. **Escreva uma linha nova no DIÁRIO DE BORDO** dizendo o último item feito e o próximo a fazer.
5. Faça o commit final.
6. Só então o usuário pode abrir um chat novo com segurança.

### 📖 DIÁRIO DE BORDO (a IA preenche — uma linha por sessão)

> Formato: `AAAA-MM-DD — última tarefa feita: X.Y — próxima: Z.W — testes: OK/N`

- (exemplo) 2026-07-10 — última: 0.2 (baseline) — próxima: 1.1 — testes: OK
<!-- A IA adiciona novas linhas ABAIXO desta. Nunca apague linhas antigas. -->
2026-07-13 — última: 0.2 (baseline 158 passed) — próxima: 1.1 — testes: OK
2026-07-13 — última: 1.1 (faster-whisper 1.2.1) — próxima: 1.2 — testes: OK
2026-07-13 — última: 1.2 (TRANSCRIBE_BACKEND config) — próxima: 1.3 — testes: OK
2026-07-13 — última: 1.3 (local_whisper.py) — próxima: 1.4 — testes: OK
2026-07-13 — última: 1.4 (transcriber local backend) — próxima: 1.5 — testes: OK
2026-07-13 — última: 1.5 (.env.example TRANSCRIBE_BACKEND) — próxima: 1.6 — testes: OK
2026-07-13 — última: 1.6 — próxima: 2A.1 — testes: OK
2026-07-13 — última: 2A.1 (ASS bold+outline) — próxima: 2A.2 — testes: OK
2026-07-13 — última: 2A.2 (visual outline/bold OK) — próxima: 2B.1 — testes: OK

### ⚠️ Regras para não conflitar entre chats

- **Faça as fases em ordem numérica.** Vários passos editam o MESMO arquivo (ex.: `subtitle_burner.py` é tocado nas Fases 2, 7 e 11). Se você pular a ordem, o texto que o passo manda "LOCALIZAR" pode não existir ainda, ou já ter mudado.
- **Dentro de uma fase, faça os subitens em ordem** (2A antes de 2B antes de 2C).
- **Só a Fase 6B e a Fase 11.3 (web) são opcionais.** O resto deve ser feito.
- Se um "LOCALIZE" não bater exatamente, é sinal de que outro item já mexeu ali ou que a ordem foi quebrada — **PARE e verifique o git**, não force.

---

## ⚠️ REGRAS PARA QUEM VAI EXECUTAR (LEIA PRIMEIRO)

1. **Faça UM item de cada vez.** Nunca comece o próximo `- [ ]` antes de terminar e testar o atual.
2. **Sempre use o venv do projeto.** Todos os comandos Python usam `.venv/bin/python` e `.venv/bin/pip`, nunca o Python global.
3. **Antes de mudar qualquer arquivo, faça um commit** (ou uma cópia). Assim dá para voltar se quebrar:
   ```bash
   git add -A && git commit -m "checkpoint antes do item X"
   ```
4. **Depois de CADA item, rode os testes:**
   ```bash
   .venv/bin/python -m pytest -q
   ```
   - Se um teste quebrar **e o item NÃO avisou que isso ia acontecer** → **desfaça sua mudança** e releia o passo (você errou algo).
   - Se o próprio item disser *"isso vai quebrar o teste X, atualize-o assim"* → então quebrar é esperado; **atualize o teste como o item mandar** e siga. Só a **Fase 3** tem esse caso.
5. **Não altere nada que o passo não mandou alterar.** Não "melhore" código vizinho. Não renomeie funções. Não remova comentários.
6. **Quando o passo disser "LOCALIZE este trecho"**, procure o texto exato no arquivo e troque **só aquele trecho**. Se não achar o texto idêntico, PARE e reporte — não invente.
7. **Todo comportamento novo deve ter um interruptor no `.env`** e um **fallback** para o comportamento antigo. Se o novo caminho falhar, o programa deve continuar funcionando como antes.
8. **Nunca apague arquivos de vídeo do usuário** (pastas `resultados/`, vídeos de entrada). Pode limpar `temp/`.
9. Os números de linha citados são só referência; **ache pelo nome da função e pelo texto**, porque as linhas mudam conforme você edita.

---

## FASE 0 — Preparação (obrigatória)

- [x] **0.1 — Confirmar ambiente.** Rode e confirme que aparece `cuda True` e que o ffmpeg tem `h264_nvenc`:
  ```bash
  .venv/bin/python -c "import torch; print('cuda', torch.cuda.is_available())"
  ffmpeg -hide_banner -encoders | grep -E "nvenc"
  ```
  Esperado: `cuda True` e linhas `h264_nvenc`, `hevc_nvenc`. Se `cuda` for `False`, PARE — as fases 1, 5, 6 e 9 dependem da GPU.

- [x] **0.2 — Rodar os testes uma vez para ter a "linha de base":**
  ```bash
  .venv/bin/python -m pytest -q
  ```
  Anote quantos passam (hoje são **158**, todos verdes). Esse é o número que você deve manter.

  **O que os testes cobrem (e o que NÃO cobrem):** são testes de **lógica pura** — parsing de JSON, cache, nomes de arquivo, filtros de áudio da batalha, seleção de encoder. Eles **não** rodam FFmpeg, crop, transcrição nem encode de verdade. Então:
  - Eles garantem que você **não quebrou o código** → rode-os sempre.
  - Eles **não** garantem que "a legenda ficou bonita" ou "o crop focou quem fala" → por isso quase todo item tem também um **teste visual** (gerar um clipe curto e olhar). Faça os dois.
  - **Exceção importante:** a Fase 3 muda uma função que tem teste próprio; lá você vai **atualizar** o teste (o item explica como). Em nenhuma outra fase você deve editar arquivos de `tests/`.

---

## FASE 1 — Transcrição local na GPU (faster-whisper)

**Objetivo:** parar de transcrever na nuvem (Groq) e passar a transcrever na GPU, mais rápido e com **timestamp por palavra** (necessário para a legenda karaokê da Fase 2).
**Arquivos:** cria `app/ai_integrations/local_whisper.py`; edita `app/ai_integrations/transcriber.py` e `app/core/config.py`.

- [x] **1.1 — Instalar a biblioteca:**
  ```bash
  .venv/bin/pip install faster-whisper
  ```

- [x] **1.2 — Adicionar config.** No fim de `app/core/config.py`, adicione:
  ```python
  # Transcrição: "local" (faster-whisper na GPU) ou "groq" (nuvem, comportamento antigo).
  TRANSCRIBE_BACKEND: str = os.getenv("TRANSCRIBE_BACKEND", "local").strip().lower()
  # Modelo do faster-whisper (large-v3 = melhor; medium = mais rápido/menos VRAM).
  LOCAL_WHISPER_MODEL: str = os.getenv("LOCAL_WHISPER_MODEL", "large-v3").strip()
  LOCAL_WHISPER_COMPUTE: str = os.getenv("LOCAL_WHISPER_COMPUTE", "float16").strip()
  ```

- [x] **1.3 — Criar o módulo `app/ai_integrations/local_whisper.py`** com este conteúdo exato:
  ```python
  """Transcrição local via faster-whisper (CTranslate2) na GPU."""
  from __future__ import annotations

  import logging
  import threading

  from app.core.config import LOCAL_WHISPER_COMPUTE, LOCAL_WHISPER_MODEL

  _log = logging.getLogger("local_whisper")
  _model = None
  _lock = threading.Lock()


  def local_whisper_available() -> bool:
      try:
          import faster_whisper  # noqa: F401
          import torch
          return bool(torch.cuda.is_available())
      except Exception:
          return False


  def _get_model():
      global _model
      with _lock:
          if _model is None:
              from faster_whisper import WhisperModel
              _log.info("Carregando faster-whisper (%s, %s) na GPU…",
                        LOCAL_WHISPER_MODEL, LOCAL_WHISPER_COMPUTE)
              _model = WhisperModel(
                  LOCAL_WHISPER_MODEL, device="cuda", compute_type=LOCAL_WHISPER_COMPUTE,
              )
          return _model


  def transcribe_local(audio_path: str, language: str | None = None) -> list[dict]:
      """Retorna [{start, end, text, words:[{start,end,word}]}], mesma forma do Groq + words."""
      model = _get_model()
      segments, _info = model.transcribe(
          audio_path,
          language=language,
          word_timestamps=True,
          vad_filter=True,
      )
      out: list[dict] = []
      for s in segments:
          words = []
          for w in (s.words or []):
              words.append({"start": float(w.start), "end": float(w.end),
                            "word": str(w.word)})
          out.append({
              "start": float(s.start),
              "end": float(s.end),
              "text": str(s.text).strip(),
              "words": words,
          })
      return out
  ```

- [x] **1.4 — Ligar o backend local em `transcriber.py`.** Abra `app/ai_integrations/transcriber.py`, LOCALIZE o início da função:
  ```python
  def transcribe_audio(audio_path: str, language: str = None, *, source_video_path: str | None = None) -> list[dict]:
  ```
  Logo abaixo do docstring dessa função (antes da linha `if not os.path.exists(audio_path):`), insira:
  ```python
      from app.core.config import TRANSCRIBE_BACKEND
      if TRANSCRIBE_BACKEND == "local":
          try:
              from app.ai_integrations.local_whisper import local_whisper_available, transcribe_local
              if local_whisper_available():
                  _log.info("Transcrição LOCAL (faster-whisper na GPU).")
                  return transcribe_local(audio_path, language)
              _log.warning("faster-whisper indisponível; caindo para Groq.")
          except Exception as e:
              _log.warning("Transcrição local falhou (%s); caindo para Groq.", e)
  ```
  **Cuidado:** isso é só um "atalho" no topo. Se cair no Groq, tudo continua como antes.

- [x] **1.5 — Documentar no `.env.example`.** Adicione ao arquivo `.env.example`:
  ```
  # Transcrição: local (GPU, rápido, dá timestamp por palavra) ou groq (nuvem)
  # TRANSCRIBE_BACKEND=local
  # LOCAL_WHISPER_MODEL=large-v3
  # LOCAL_WHISPER_COMPUTE=float16
  ```

- [x] **1.6 — Testar.** Rode o pipeline com um vídeo curto (10–30 s) que tenha fala:
  ```bash
  .venv/bin/python main.py CAMINHO/DO/video_curto.mp4
  ```
  No log deve aparecer **"Transcrição LOCAL (faster-whisper na GPU)"**. O vídeo final em `resultados/` deve ter legenda correta. Rode `pytest` também.

---

## FASE 2 — Legenda estilo TikTok (karaokê + contorno + fonte)

**Objetivo:** legenda com palavras "saltando" (karaokê), contorno grosso no lugar da caixa, e fonte bold.
**Arquivos:** `app/subtitle/ass_builder.py`, `app/core/config.py`, `app/video_processing/subtitle_burner.py`, pasta nova `assets/fonts/`.

### 2A — Trocar caixa opaca por contorno + negrito (rápido e seguro)

- [x] **2A.1** — Abra `app/subtitle/ass_builder.py`, dentro de `write_tiktok_ass_from_srt`, LOCALIZE:
  ```python
      style = (
          f"Style: Default,{font_name},{font_size},{primary_ass},&H000000FF,&H00000000,"
          f"{back_ass},0,0,0,0,100,100,0,0,4,0,0,{alignment},"
          f"{margin_l},{margin_r},{margin_v},1"
      )
  ```
  Troque **somente** o miolo `,0,0,0,0,100,100,0,0,4,0,0,` por `,1,0,0,0,100,100,0,0,1,4,1,`. O resultado deve ficar:
  ```python
      style = (
          f"Style: Default,{font_name},{font_size},{primary_ass},&H000000FF,&H00000000,"
          f"{back_ass},1,0,0,0,100,100,0,0,1,4,1,{alignment},"
          f"{margin_l},{margin_r},{margin_v},1"
      )
  ```
  **O que isso faz:** `1` (negrito), `1` (BorderStyle = contorno em vez de caixa), `4` (grossura do contorno), `1` (sombra). O texto passa a ter borda preta grossa — o visual moderno.

- [x] **2A.2 — Testar:** gere um clipe (como em 1.6) e confira visualmente que a legenda tem contorno preto e está em negrito, sem a caixa retangular. Rode `pytest`.

### 2B — Fonte bold empacotada

- [ ] **2B.1** — Crie a pasta `assets/fonts/` e coloque nela um arquivo TTF de fonte bold (ex.: `Montserrat-Bold.ttf` ou `Anton-Regular.ttf`). Baixe uma fonte gratuita/comercialmente livre.

- [ ] **2B.2** — No fim de `app/core/config.py` adicione:
  ```python
  # Nome interno da fonte da legenda (deve bater com o "name" do TTF em assets/fonts/)
  TIKTOK_SUBTITLE_FONT: str = os.getenv("TIKTOK_SUBTITLE_FONT", "Montserrat").strip()
  FONTS_DIR: str = str((Path(__file__).resolve().parents[2] / "assets" / "fonts"))
  ```

- [ ] **2B.3** — Em `app/video_processing/subtitle_burner.py`, LOCALIZE a linha que monta o filtro de legenda (dentro de `_prepare_scale_crop_overlay_vf`):
  ```python
      vf = f"{scale_crop},subtitles='{escaped}'{hook_vf}{cta_vf}"
  ```
  Troque por (passa a pasta de fontes para o libass achar o TTF):
  ```python
      from app.core.config import FONTS_DIR
      fonts_clause = f":fontsdir='{_escape_srt_path(FONTS_DIR)}'"
      vf = f"{scale_crop},subtitles='{escaped}'{fonts_clause}{hook_vf}{cta_vf}"
  ```

- [ ] **2B.4** — Ainda em `subtitle_burner.py`, garanta que a fonte usada é a nova. Isso é controlado pelo parâmetro `fonte` que vem de fora com default `"Arial"`. Não mude a assinatura; em vez disso, no começo de `_prepare_scale_crop_overlay_vf`, logo após o `def ...:` e o docstring, adicione:
  ```python
      from app.core.config import TIKTOK_SUBTITLE_FONT
      if fonte in (None, "", "Arial"):
          fonte = TIKTOK_SUBTITLE_FONT
  ```
  **Cuidado:** o "name" interno do TTF precisa bater com `TIKTOK_SUBTITLE_FONT`. Se a legenda sair na fonte errada, ajuste `TIKTOK_SUBTITLE_FONT` no `.env` para o nome real da família da fonte.

- [ ] **2B.5 — Testar:** gere um clipe e confira que a fonte mudou. `pytest`.

### 2C — Karaokê (palavras saltando)

> Esta versão funciona **sempre**, mesmo com legenda traduzida: ela divide o tempo de cada linha igualmente entre as palavras. Não depende de mexer no resto do pipeline.

- [ ] **2C.1** — No fim de `app/core/config.py` adicione:
  ```python
  # Legenda karaokê (palavra a palavra). 1 = ligado.
  SUBTITLE_KARAOKE: bool = os.getenv("SUBTITLE_KARAOKE", "1").strip().lower() in ("1","true","yes","on")
  # Cor de destaque da palavra ativa (hex). Base fica branca.
  SUBTITLE_KARAOKE_HIGHLIGHT: str = os.getenv("SUBTITLE_KARAOKE_HIGHLIGHT", "#FFE000").strip()
  ```

- [ ] **2C.2** — Em `app/subtitle/ass_builder.py`, adicione esta função nova **no fim do arquivo** (não apague a existente):
  ```python
  def _hex_to_ass(hex_color: str) -> str:
      h = (hex_color or "#FFFFFF").lstrip("#")
      if len(h) != 6:
          h = "FFFFFF"
      r, g, b = h[0:2], h[2:4], h[4:6]
      return f"&H00{b}{g}{r}".upper()


  def write_tiktok_ass_karaoke_from_srt(
      srt_path: str,
      ass_path: str,
      *,
      play_res_x: int,
      play_res_y: int,
      font_name: str,
      font_size: int,
      highlight_hex: str,
      margin_l: int,
      margin_r: int,
      margin_v: int,
      alignment: int,
  ) -> str:
      """Igual ao write_tiktok_ass_from_srt, mas com karaokê (\\k) por palavra.
      A palavra 'já falada' fica na cor de destaque; distribui o tempo da linha
      igualmente entre as palavras (aproximação que funciona mesmo sem word timestamps)."""
      Path(ass_path).parent.mkdir(parents=True, exist_ok=True)
      content = Path(srt_path).read_text(encoding="utf-8-sig")

      primary = _hex_to_ass(highlight_hex)   # cor de quem JÁ foi falado
      secondary = _hex_to_ass("#FFFFFF")     # cor de quem ainda NÃO foi falado
      outline = _hex_to_ass("#000000")

      style = (
          f"Style: Default,{font_name},{font_size},{primary},{secondary},{outline},"
          f"&H64000000,1,0,0,0,100,100,0,0,1,4,1,{alignment},"
          f"{margin_l},{margin_r},{margin_v},1"
      )
      header = (
          "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
          f"PlayResX: {play_res_x}\nPlayResY: {play_res_y}\n\n"
          "[V4+ Styles]\n"
          "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
          "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
          "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
          f"{style}\n\n[Events]\n"
          "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
      )

      def _cs(ass_ts: str) -> int:
          # "h:mm:ss.cc" -> centésimos totais
          h, m, rest = ass_ts.split(":")
          s, cs = rest.split(".")
          return ((int(h) * 3600 + int(m) * 60 + int(s)) * 100) + int(cs)

      events: list[str] = []
      for start_s, end_s, body in _iter_srt_entries(content):
          if not body:
              continue
          a0 = _srt_timestamp_to_ass(start_s)
          a1 = _srt_timestamp_to_ass(end_s)
          total = max(1, _cs(a1) - _cs(a0))
          words = _escape_ass_text(body).split()
          if not words:
              continue
          per = max(1, total // len(words))
          chunks = []
          acc = 0
          for i, w in enumerate(words):
              dur = per if i < len(words) - 1 else max(1, total - acc)
              acc += dur
              chunks.append(f"{{\\k{dur}}}{w} ")
          text = "".join(chunks).strip()
          events.append(f"Dialogue: 0,{a0},{a1},Default,,0,0,0,,{text}")

      Path(ass_path).write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
      return ass_path
  ```

- [ ] **2C.3** — Em `app/video_processing/subtitle_burner.py`, no topo, LOCALIZE o import:
  ```python
  from app.subtitle.ass_builder import write_tiktok_ass_from_srt
  ```
  Troque por:
  ```python
  from app.subtitle.ass_builder import write_tiktok_ass_from_srt, write_tiktok_ass_karaoke_from_srt
  ```

- [ ] **2C.4** — Ainda em `subtitle_burner.py`, dentro de `_prepare_scale_crop_overlay_vf`, LOCALIZE o bloco que chama `write_tiktok_ass_from_srt(...)`:
  ```python
      ass_path = str(Path(srt_path).with_suffix(".ass"))
      write_tiktok_ass_from_srt(
          srt_path,
          ass_path,
          play_res_x=w,
          play_res_y=h,
          font_name=fonte,
          font_size=fs,
          primary_ass=primary,
          back_ass=back,
          margin_l=mlr,
          margin_r=mlr,
          margin_v=subtitle_margin_v,
          alignment=alignment,
      )
  ```
  Troque por:
  ```python
      from app.core.config import SUBTITLE_KARAOKE, SUBTITLE_KARAOKE_HIGHLIGHT
      ass_path = str(Path(srt_path).with_suffix(".ass"))
      if SUBTITLE_KARAOKE:
          write_tiktok_ass_karaoke_from_srt(
              srt_path,
              ass_path,
              play_res_x=w,
              play_res_y=h,
              font_name=fonte,
              font_size=fs,
              highlight_hex=SUBTITLE_KARAOKE_HIGHLIGHT,
              margin_l=mlr,
              margin_r=mlr,
              margin_v=subtitle_margin_v,
              alignment=alignment,
          )
      else:
          write_tiktok_ass_from_srt(
              srt_path,
              ass_path,
              play_res_x=w,
              play_res_y=h,
              font_name=fonte,
              font_size=fs,
              primary_ass=primary,
              back_ass=back,
              margin_l=mlr,
              margin_r=mlr,
              margin_v=subtitle_margin_v,
              alignment=alignment,
          )
  ```

- [ ] **2C.5 — Testar:** gere um clipe com fala. As palavras devem ir mudando de cor (branco → amarelo) conforme a fala avança. Se quiser desligar, ponha `SUBTITLE_KARAOKE=0` no `.env`. Rode `pytest`.

---

## FASE 3 — NVENC em TODOS os clipes + presets de qualidade

**Objetivo:** usar a GPU (NVENC) em todos os clipes (hoje só os últimos) e melhorar o preset.
**Arquivos:** `app/pipelines/cortes/pipeline.py`, `app/core/config.py`.

- [ ] **3.1** — Abra `app/pipelines/cortes/pipeline.py`, LOCALIZE a função:
  ```python
  def _clip_uses_gpu_encoder(clip_index: int, total_clips: int) -> bool:
      """Últimos clipes com encoder de GPU (AMF/NVENC/QSV); restante em libx264 no CPU."""
      if not USE_GPU_CLIP_ENCODE:
          return False
      need = CLIP_ENCODE_PARALLEL_CPU + CLIP_ENCODE_PARALLEL_GPU
      if total_clips < need:
          return False
      return clip_index > total_clips - CLIP_ENCODE_PARALLEL_GPU
  ```
  Troque o corpo inteiro por:
  ```python
  def _clip_uses_gpu_encoder(clip_index: int, total_clips: int) -> bool:
      """Todos os clipes usam o encoder de GPU (NVENC) quando habilitado."""
      return USE_GPU_CLIP_ENCODE
  ```
  **Por que é seguro:** se o NVENC falhar em algum clipe, `cut_and_burn_subtitles` já tem fallback automático para CPU (libx264).

- [ ] **3.1b — Atualizar o teste que trava o comportamento ANTIGO (esperado quebrar).** A mudança 3.1 vai fazer `tests/test_pipeline_gpu_heuristic.py` falhar, porque ele testa a regra antiga ("só os últimos clipes usam GPU"). Abra `tests/test_pipeline_gpu_heuristic.py` e substitua as duas funções `test_gpu_on_but_few_clips` e `test_gpu_on_last_indices` por estas (agora TODOS os clipes usam GPU quando ligado):
  ```python
  def test_gpu_on_all_clips_use_gpu(gpu_on) -> None:
      # Agora todos os clipes usam GPU quando USE_GPU_CLIP_ENCODE está ligado.
      assert _clip_uses_gpu_encoder(1, 10) is True
      assert _clip_uses_gpu_encoder(5, 10) is True
      assert _clip_uses_gpu_encoder(10, 10) is True
      assert _clip_uses_gpu_encoder(1, 1) is True
  ```
  (mantenha `test_gpu_off_never` como está — ele continua válido). Depois rode `pytest` e confirme que voltou a ficar tudo verde.

- [ ] **3.2** — Em `app/core/config.py`, LOCALIZE o ramo do NVENC dentro de `gpu_clip_encoder_ffmpeg_args()`:
  ```python
      if enc in ("h264_nvenc", "nvenc"):
          return [
              "-c:v",
              "h264_nvenc",
              "-preset",
              "p5",
              "-tune",
              "hq",
              "-rc",
              "vbr",
              "-cq",
              "21",
              "-spatial_aq",
              "1",
              "-pix_fmt",
              "yuv420p",
          ]
  ```
  Troque por (mais qualidade, sua GPU aguenta):
  ```python
      if enc in ("h264_nvenc", "nvenc"):
          return [
              "-c:v",
              "h264_nvenc",
              "-preset",
              "p6",
              "-tune",
              "hq",
              "-rc",
              "vbr",
              "-cq",
              "19",
              "-spatial_aq",
              "1",
              "-bf",
              "3",
              "-b_ref_mode",
              "middle",
              "-rc-lookahead",
              "20",
              "-pix_fmt",
              "yuv420p",
          ]
  ```

- [ ] **3.3** — No seu `.env`, coloque (mais encodes em paralelo na GPU de 16 GB):
  ```
  CLIP_ENCODE_PARALLEL_GPU=4
  ```

- [ ] **3.4 — Testar:** gere um vídeo com vários clipes. Confirme no log que o encode roda e que os MP4 saem certos em `resultados/`. Rode `pytest`.

---

## FASE 4 — NVENC na Batalha e na História (hoje usam CPU)

### 4A — Batalha

- [ ] **4A.1** — Abra `app/pipelines/batalha/batalha_ffmpeg.py`. No topo do arquivo, junto dos outros imports de `app.core.config`, adicione (se ainda não houver):
  ```python
  from app.core.config import gpu_clip_encoder_ffmpeg_args
  ```

- [ ] **4A.2** — LOCALIZE, dentro de `encode_simulation_to_silent_mp4`, este trecho da lista `cmd`:
  ```python
          "-an",
          "-c:v",
          "libx264",
          "-pix_fmt",
          "yuv420p",
          "-preset",
          "veryfast",
          "-crf",
          str(VIDEO_CRF),
          str(out_path),
  ```
  Troque por:
  ```python
          "-an",
          *gpu_clip_encoder_ffmpeg_args(),
          str(out_path),
  ```
  **Cuidado:** `gpu_clip_encoder_ffmpeg_args()` já inclui `-c:v` e `-pix_fmt`. Se em algum PC não houver GPU, essa função devolve `libx264` automaticamente, então continua funcionando.

- [ ] **4A.3 — Testar:** gere uma batalha pela GUI (aba Batalha) e confira que o MP4 sai correto. `pytest`.

### 4B — História

- [ ] **4B.1** — Abra `app/pipelines/historia/historia_pipeline.py`. Junto dos imports de `app.core.config`, adicione:
  ```python
  from app.core.config import gpu_clip_encoder_ffmpeg_args
  ```

- [ ] **4B.2** — Dentro de `_mux_cena_video_audio`, LOCALIZE:
  ```python
          "-shortest",
          "-c:v",
          "libx264",
          "-pix_fmt",
          "yuv420p",
          "-c:a",
          "aac",
          str(output_path),
  ```
  Troque por:
  ```python
          "-shortest",
          *gpu_clip_encoder_ffmpeg_args(),
          "-c:a",
          "aac",
          str(output_path),
  ```

- [ ] **4B.3 — Testar:** gere uma história (aba História) e confira o MP4. `pytest`.

---

## FASE 5 — Crop mais suave (pan, headroom, zona morta)

**Objetivo:** o enquadramento deslizar suave em vez de saltar, e enquadrar melhor (rosto no terço superior).
**Arquivo:** `app/video_processing/focal_crop.py`.

- [ ] **5.1 — Suavizar a trilha (média móvel exponencial).** Em `focal_crop.py`, adicione esta função nova logo **antes** de `def _speaker_timeline_crop_segments(`:
  ```python
  def _smooth_samples_ema(
      samples: list[tuple[float, float, float, int]],
      alpha: float = 0.35,
  ) -> list[tuple[float, float, float, int]]:
      """Suaviza (cx, cy) com EMA para o crop 'deslizar' em vez de saltar."""
      if not samples:
          return samples
      out: list[tuple[float, float, float, int]] = []
      _, sx, sy, spk0 = samples[0]
      ema_x, ema_y = sx, sy
      for (t, x, y, spk) in samples:
          ema_x = alpha * x + (1 - alpha) * ema_x
          ema_y = alpha * y + (1 - alpha) * ema_y
          out.append((t, ema_x, ema_y, spk))
      return out
  ```

- [ ] **5.2 — Aplicar a suavização.** Dentro de `_speaker_timeline_crop_segments`, LOCALIZE:
  ```python
      samples = _stabilize_speaker_changes_min_interval(
          samples, SMART_CROP_MIN_CHANGE_INTERVAL_SEC
      )
  ```
  Logo **abaixo** dessa linha, adicione:
  ```python
      samples = _smooth_samples_ema(samples, alpha=0.35)
  ```

- [ ] **5.3 — Headroom (rosto no terço superior).** Em `focal_crop.py`, LOCALIZE a função `_clamp_crop_xy` e, dentro dela, LOCALIZE:
  ```python
      y = int(round(cy_scaled - out_h / 2))
  ```
  Troque por (sobe o alvo em ~8% da altura de saída, deixando "ar" acima da cabeça):
  ```python
      y = int(round(cy_scaled - out_h / 2 - out_h * 0.08))
  ```

- [ ] **5.4 — Testar:** gere um clipe de uma pessoa falando e confira que (a) o enquadramento não "pula", e (b) o rosto não fica colado no centro, mas um pouco acima. `pytest`.

---

## FASE 6 — Crop para 2+ pessoas (foco no falante + modo faixas)

> Faça a parte **6A primeiro** (mais segura). A **6B (split)** é a mais difícil; só comece depois que a 6A estiver funcionando e testada.

### 6A — Foco no falante guiado por áudio (resolve o "se perde")

**Ideia:** hoje o "quem fala" é decidido só por movimento de pixels — qualquer mexida rouba o foco. Vamos permitir troca de falante **apenas quando há voz** naquele instante. Nos silêncios, o crop fica parado.

- [ ] **6A.1 — Detectar intervalos de voz.** Em `app/video_processing/focal_crop.py`, adicione no topo o import:
  ```python
  from app.core.config import FFMPEG_PATH
  ```
  e adicione esta função nova antes de `compute_crop_plan`:
  ```python
  def _voiced_intervals(video_path: str, clip_start: float, clip_end: float) -> list[tuple[float, float]]:
      """Intervalos (em segundos, relativos ao início do clipe) onde há voz.
      Usa silencedetect e inverte. Se algo falhar, devolve [] (sem gate)."""
      import re
      import subprocess
      try:
          dur = max(0.1, float(clip_end) - float(clip_start))
          p = subprocess.run(
              [FFMPEG_PATH, "-ss", str(clip_start), "-t", str(dur), "-i", video_path,
               "-af", "silencedetect=noise=-30dB:d=0.3", "-f", "null", "-"],
              capture_output=True, text=True, check=False,
          )
          text = p.stderr or ""
          silences: list[tuple[float, float]] = []
          cur = None
          for line in text.splitlines():
              m1 = re.search(r"silence_start:\s*([\d.]+)", line)
              m2 = re.search(r"silence_end:\s*([\d.]+)", line)
              if m1:
                  cur = float(m1.group(1))
              elif m2 and cur is not None:
                  silences.append((cur, float(m2.group(1))))
                  cur = None
          # inverte silêncios -> voz
          voiced: list[tuple[float, float]] = []
          t = 0.0
          for s0, s1 in silences:
              if s0 > t:
                  voiced.append((t, s0))
              t = max(t, s1)
          if t < dur:
              voiced.append((t, dur))
          return voiced
      except Exception:
          return []


  def _is_voiced(t: float, voiced: list[tuple[float, float]]) -> bool:
      if not voiced:
          return True  # sem info -> permite (comportamento antigo)
      for a, b in voiced:
          if a <= t <= b:
              return True
      return False
  ```

- [ ] **6A.2 — Passar os intervalos de voz para o cálculo do falante.** Em `_speaker_timeline_crop_segments`, adicione um parâmetro novo. LOCALIZE a assinatura:
  ```python
  def _speaker_timeline_crop_segments(
      cap: object,
      face_detector,
      out_w: int,
      out_h: int,
      fps_sample: float,
      *,
      time_offset_sec: float = 0.0,
      clip_duration_sec: float | None = None,
  ) -> Optional[list[tuple[float, float, int, int]]]:
  ```
  Troque por (adiciona `voiced`):
  ```python
  def _speaker_timeline_crop_segments(
      cap: object,
      face_detector,
      out_w: int,
      out_h: int,
      fps_sample: float,
      *,
      time_offset_sec: float = 0.0,
      clip_duration_sec: float | None = None,
      voiced: list[tuple[float, float]] | None = None,
  ) -> Optional[list[tuple[float, float, int, int]]]:
  ```

- [ ] **6A.3 — Usar o gate.** Dentro dessa mesma função, LOCALIZE o bloco que escolhe `spk` quando há 2+ rostos. Ele termina decidindo `spk`. Logo **depois** do bloco `if len(faces) >= 2 ... else: spk = 0` e **antes** da linha `cx, cy = faces[spk][0], faces[spk][1]`, insira:
  ```python
          # Só troca de falante durante voz; no silêncio mantém o anterior.
          if voiced is not None and not _is_voiced(t, voiced):
              if last_speaker_i < len(faces):
                  spk = last_speaker_i
  ```

- [ ] **6A.4 — Calcular e repassar `voiced` em `compute_crop_plan`.** Em `compute_crop_plan`, LOCALIZE onde é chamado `_speaker_timeline_crop_segments(` e adicione o argumento `voiced=`. Primeiro, logo após `use_clip = clip_start is not None and clip_end is not None`, calcule:
  ```python
              voiced = _voiced_intervals(video_path, float(clip_start), float(clip_end)) if use_clip else None
  ```
  Depois, na chamada:
  ```python
              segs = _speaker_timeline_crop_segments(
                  cap,
                  fd,
                  out_w,
                  out_h,
                  SMART_CROP_SPEAKER_FPS,
                  time_offset_sec=float(clip_start) if use_clip else 0.0,
                  clip_duration_sec=clip_len if use_clip else None,
              )
  ```
  adicione `voiced=voiced,` como último argumento antes do `)`.

- [ ] **6A.5 — Testar:** gere um clipe de podcast/entrevista com 2 pessoas. O foco deve ir para quem fala e **não** trocar quando a outra pessoa só balança a cabeça em silêncio. `pytest`.

### 6B — Modo faixas (split empilhado) — AVANÇADO

**Ideia:** quando há 2 rostos muito afastados, em vez de escolher um, dividir a tela: pessoa de cima / pessoa de baixo.

- [ ] **6B.1 — Config.** No fim de `app/core/config.py`:
  ```python
  # Crop: usar layout empilhado (faixas) quando 2 rostos estão muito afastados.
  SMART_CROP_SPLIT_ENABLED: bool = os.getenv("SMART_CROP_SPLIT_ENABLED", "1").strip().lower() in ("1","true","yes","on")
  ```

- [ ] **6B.2 — Detectar posições das 2 pessoas e decidir split.** Em `focal_crop.py`, adicione uma função que devolve os centros medianos de cada uma das 2 pessoas. Adicione antes de `compute_crop_plan`:
  ```python
  def _two_people_centers(cap, face_detector, out_w, out_h, *, clip_start, clip_end):
      """Devolve ((cxL,cyL),(cxR,cyR)) em pixels da fonte, ou None."""
      import cv2, statistics
      vfps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
      dur = max(0.1, float(clip_end) - float(clip_start))
      n = max(5, min(SMART_CROP_FRAME_SAMPLES, int(dur * 2)))
      left_x, left_y, right_x, right_y = [], [], [], []
      for i in range(n):
          ts = float(clip_start) + (i + 0.5) / n * dur
          cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
          ok, frame = cap.read()
          if not ok or frame is None:
              continue
          faces = _all_faces_sorted_by_x(frame, face_detector)
          if len(faces) < 2:
              continue
          l, r = faces[0], faces[-1]
          left_x.append(l[0]); left_y.append(l[1])
          right_x.append(r[0]); right_y.append(r[1])
      if len(left_x) < 3 or len(right_x) < 3:
          return None
      return (
          (statistics.median(left_x), statistics.median(left_y)),
          (statistics.median(right_x), statistics.median(right_y)),
      )
  ```

- [ ] **6B.3 — Gerar um plano "split".** Em `compute_crop_plan`, LOCALIZE o ponto em que `max_faces >= 2` (logo antes de chamar `_speaker_timeline_crop_segments`). Antes disso, insira:
  ```python
              from app.core.config import SMART_CROP_SPLIT_ENABLED
              if SMART_CROP_SPLIT_ENABLED and max_faces >= 2 and use_clip:
                  centers = _two_people_centers(
                      cap, fd, out_w, out_h, clip_start=float(clip_start), clip_end=float(clip_end)
                  )
                  if centers is not None:
                      (lx, _ly), (rx, _ry) = centers
                      src_w2 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
                      # Se os dois estão longe (> 35% da largura), usa faixas.
                      if src_w2 > 0 and abs(rx - lx) > 0.35 * src_w2:
                          return {"mode": "split", "left": centers[0], "right": centers[1]}
  ```

- [ ] **6B.4 — Renderizar o split.** Este é o passo mais delicado, porque o `split` precisa de `-filter_complex` (não cabe no `-vf` simples). Em `app/video_processing/subtitle_burner.py`, o encode usa `-vf`. Para o modo split, faça o seguinte **na função `cut_and_burn_subtitles`**:

  a) O plano de crop é calculado dentro de `_prepare_scale_crop_overlay_vf`. Precisamos saber se veio "split". A forma mais simples: em `_prepare_scale_crop_overlay_vf`, quando o `plan["mode"] == "split"`, monte um filtergraph de faixas e RETORNE ele. LOCALIZE, dentro dessa função, o bloco:
  ```python
          if plan is not None:
              if plan["mode"] == "static":
                  scale_crop += f",crop={w}:{h}:{plan['x']}:{plan['y']}"
              else:
                  scale_crop += f",crop={w}:{h}:{plan['x_expr']}:{plan['y_expr']}"
  ```
  Troque por:
  ```python
          if plan is not None:
              if plan["mode"] == "static":
                  scale_crop += f",crop={w}:{h}:{plan['x']}:{plan['y']}"
              elif plan["mode"] == "split":
                  # cada faixa: recorta metade da tela em volta de cada pessoa
                  (lx, ly), (rx, ry) = plan["left"], plan["right"]
                  half = h // 2
                  cw = w
                  # recorte quadrado-ish ao redor de cada pessoa, depois escala p/ faixa
                  scale_crop = (
                      f"[0:v]crop={cw}:{half}:{int(max(0, lx - cw/2))}:{int(max(0, ly - half/2))},"
                      f"scale={w}:{half}:force_original_aspect_ratio=increase,crop={w}:{half},setsar=1[top];"
                      f"[0:v]crop={cw}:{half}:{int(max(0, rx - cw/2))}:{int(max(0, ry - half/2))},"
                      f"scale={w}:{half}:force_original_aspect_ratio=increase,crop={w}:{half},setsar=1[bot];"
                      f"[top][bot]vstack=inputs=2[vsplit]"
                  )
                  # sinaliza que este vf é um filter_complex (começa com "[0:v]")
              else:
                  scale_crop += f",crop={w}:{h}:{plan['x_expr']}:{plan['y_expr']}"
  ```

  b) Como o split usa `-filter_complex` e um label de saída, e o resto (legenda/hook/cta) precisa vir depois, esta parte exige que a chamada FFmpeg troque `-vf X` por `-filter_complex "...;[vsplit]subtitles=...[vout]" -map "[vout]"`. **Se isso for complexo demais para você implementar com segurança, PARE a 6B aqui e deixe `SMART_CROP_SPLIT_ENABLED=0` no `.env`.** A 6A sozinha já resolve a maior parte do problema. Marque este item como "adiado" e siga para a Fase 7.

- [ ] **6B.5 — Testar (se implementou):** clipe com 2 pessoas afastadas deve sair com tela dividida (uma em cima, outra embaixo), cada rosto enquadrado. Se sair errado, ponha `SMART_CROP_SPLIT_ENABLED=0`. `pytest`.

---

## FASE 7 — Identidade visual (barra de progresso, grade de cor, marca)

**Objetivo:** dar um padrão visual sutil, tudo no MESMO passe FFmpeg (sem deixar mais lento).
**Arquivo:** `app/video_processing/subtitle_burner.py` e `app/core/config.py`.

- [ ] **7.1 — Config.** No fim de `app/core/config.py`:
  ```python
  # Identidade visual (tudo no passe único; custo ~zero)
  VISUAL_GRADE: bool = os.getenv("VISUAL_GRADE", "1").strip().lower() in ("1","true","yes","on")
  VISUAL_PROGRESS_BAR: bool = os.getenv("VISUAL_PROGRESS_BAR", "1").strip().lower() in ("1","true","yes","on")
  VISUAL_PROGRESS_COLOR: str = os.getenv("VISUAL_PROGRESS_COLOR", "yellow").strip()
  VISUAL_WATERMARK_TEXT: str = os.getenv("VISUAL_WATERMARK_TEXT", "").strip()  # ex.: "@seuperfil"
  ```

- [ ] **7.2 — Aplicar grade + barra + marca.** Em `subtitle_burner.py`, na função `cut_and_burn_subtitles`, LOCALIZE:
  ```python
      vf = f"{vf_cut},{vf_overlay}"
  ```
  Troque por:
  ```python
      from app.core.config import VISUAL_GRADE, VISUAL_PROGRESS_BAR, VISUAL_PROGRESS_COLOR, VISUAL_WATERMARK_TEXT
      extra = ""
      if VISUAL_GRADE:
          extra += ",eq=contrast=1.06:saturation=1.12:brightness=0.01,vignette=PI/6"
      if VISUAL_PROGRESS_BAR:
          dur_pb = max(0.1, (float(clip_end) - float(clip_start)) / (1.0 + CLIP_SPEED_UP_PERCENT / 100.0))
          extra += f",drawbox=x=0:y=0:w='iw*t/{dur_pb:.3f}':h=8:color={VISUAL_PROGRESS_COLOR}@0.9:t=fill"
      if VISUAL_WATERMARK_TEXT:
          wm = _escape_filter_single_quoted(VISUAL_WATERMARK_TEXT)
          extra += (
              f",drawtext=text='{wm}':font='Arial':fontsize=34:fontcolor=white@0.75:"
              f"x=w-text_w-40:y=h-text_h-40:borderw=2:bordercolor=black@0.6"
          )
      vf = f"{vf_cut},{vf_overlay}{extra}"
  ```
  **Cuidado:** a ordem importa — `vf_overlay` já inclui a legenda; o `extra` (grade/barra/marca) vem depois. Está correto assim.

- [ ] **7.3 — Testar:** gere um clipe. Deve aparecer: cores um pouco mais vivas, leve escurecimento nas bordas, uma barrinha crescendo no topo, e (se você preencher `VISUAL_WATERMARK_TEXT` no `.env`) o @ no canto. Se algo ficar feio, desligue com as flags no `.env`. `pytest`.

---

## FASE 8 — Análise viral cobrir o vídeo inteiro

**Objetivo:** hoje só os primeiros ~10.000 caracteres da transcrição vão para a IA (em vídeo longo, só o começo é analisado). Aumentar a cobertura.
**Arquivo:** `app/ai_integrations/viral_analyzer.py`.

- [ ] **8.1 — Aumentar o teto (ganho imediato, risco baixo).** LOCALIZE:
  ```python
  _MAX_TRANSCRIPT_CHARS = 10_000
  ```
  Troque por:
  ```python
  _MAX_TRANSCRIPT_CHARS = 24_000
  ```
  **Cuidado:** não exagere; muito acima disso o modelo Groq pode recusar por prompt grande. 24k é um meio-termo seguro.

- [ ] **8.2 — Amostrar o vídeo todo em vez de só o começo.** Em `_build_transcript_text`, LOCALIZE:
  ```python
      linhas = []
      tamanho_atual = 0

      for seg in segments:
          linha = f"[{seg['start']:.0f}s] {seg['text']}"
          if tamanho_atual + len(linha) > _MAX_TRANSCRIPT_CHARS:
              break
          linhas.append(linha)
          tamanho_atual += len(linha) + 1

      return "\n".join(linhas)
  ```
  Troque por (pega segmentos espaçados ao longo do vídeo inteiro se não couber tudo):
  ```python
      linhas_full = [f"[{seg['start']:.0f}s] {seg['text']}" for seg in segments]
      total = sum(len(x) + 1 for x in linhas_full)
      if total <= _MAX_TRANSCRIPT_CHARS:
          return "\n".join(linhas_full)
      # Não cabe tudo: amostra uniformemente do início ao fim (cobre o vídeo inteiro).
      import math
      keep = max(1, int(len(linhas_full) * _MAX_TRANSCRIPT_CHARS / total))
      step = max(1, math.ceil(len(linhas_full) / keep))
      amostra = linhas_full[::step]
      return "\n".join(amostra)
  ```

- [ ] **8.3 — Testar:** rode com um vídeo LONGO (se tiver) e confira que os momentos escolhidos (no log/manifest em `resultados/*.json`) têm timestamps espalhados, não só no começo. `pytest`.

---

## FASE 9 — Dublagem via Kokoro (GPU) e correções

**Objetivo:** dublar usando a voz local na GPU (rápido, offline) em vez de sempre a nuvem (Edge). E corrigir 2 bugs.
**Arquivo:** `app/video_processing/tts_dubber.py`.

- [ ] **9.1 — Corrigir o retry de timeout do Edge (bug).** LOCALIZE em `_edge_tts_save`:
  ```python
          except asyncio.TimeoutError as e:
              raise RuntimeError(
                  f"Edge-TTS excedeu {timeout:.0f}s neste trecho. "
                  "Aumente EDGE_TTS_REQUEST_TIMEOUT_SEC no .env ou tente de novo."
              ) from e
  ```
  Troque por (deixa o timeout ser retentado como as outras falhas):
  ```python
          except asyncio.TimeoutError as e:
              last_err = e
              if attempt >= EDGE_TTS_RETRIES - 1:
                  raise RuntimeError(
                      f"Edge-TTS excedeu {timeout:.0f}s neste trecho. "
                      "Aumente EDGE_TTS_REQUEST_TIMEOUT_SEC no .env ou tente de novo."
                  ) from e
              await asyncio.sleep(min(48.0, (2**attempt) + random.random()))
              continue
  ```

- [ ] **9.2 — Rotear a síntese por Kokoro quando disponível.** Em `tts_dubber.py`, LOCALIZE a função `_run_edge_tts_parallel` (a que sintetiza os trechos). Antes dela, adicione:
  ```python
  def _synthesize_jobs(jobs: list[tuple[str, Path]], voice: str) -> None:
      """Sintetiza cada (texto, arquivo). Usa Kokoro local (GPU) se der; senão Edge."""
      try:
          from app.tts.local_tts import local_tts_available, local_tts_save_to_path
          from app.core.config import LOCAL_TTS_PREFERRED
          if LOCAL_TTS_PREFERRED and local_tts_available():
              for tx, op in jobs:
                  local_tts_save_to_path(tx, str(op))  # usa a voz pt-BR padrão do Kokoro
              return
      except Exception as _e:
          pass
      _run_edge_tts_parallel(jobs, voice)
  ```

- [ ] **9.3 — Usar o novo roteador.** Em `build_dub_audio`, LOCALIZE a chamada:
  ```python
          _run_edge_tts_parallel(tts_jobs, voice)
  ```
  Troque por:
  ```python
          _synthesize_jobs(tts_jobs, voice)
  ```
  **Cuidado:** o Kokoro grava MP3; os arquivos de trabalho aqui são `raw_{i}.mp3` — compatível. Se o Kokoro falhar, cai no Edge automaticamente.

- [ ] **9.4 — Testar:** gere um clipe com dublagem ligada (opção `--dub-pt` no CLI ou o toggle na GUI). Confira que a voz sai e está sincronizada. Se você não usa dublagem, pode marcar como feito sem testar. `pytest`.

---

## FASE 10 — Download yt-dlp mais rápido

**Objetivo:** baixar em várias conexões.
**Arquivo:** `app/download/ytdlp_download.py`.

- [ ] **10.1 — Achar onde os argumentos de download são montados.** Abra `app/download/ytdlp_download.py` e procure a lista de argumentos passada ao yt-dlp que inclui `--newline` (função de download). 

- [ ] **10.2 — Adicionar concorrência de fragmentos.** Nessa mesma lista de argumentos, adicione os itens:
  ```python
      "--concurrent-fragments", os.getenv("YTDLP_CONCURRENT_FRAGMENTS", "4"),
  ```
  (coloque perto de onde estão `--newline` / formato). Garanta que `import os` já existe no arquivo (existe).

- [ ] **10.3 — Documentar no `.env.example`:**
  ```
  # Download: nº de fragmentos em paralelo por vídeo (mais rápido em DASH/HLS)
  # YTDLP_CONCURRENT_FRAGMENTS=4
  ```

- [ ] **10.4 — Testar:** baixe um vídeo por URL pela GUI/CLI e confira que baixa normalmente (e mais rápido). `pytest`.

---

## FASE 11 — Polimento (baixo risco)

- [ ] **11.1 — Normalizar volume do áudio dos cortes.** Em `app/video_processing/subtitle_burner.py`, dentro de `cut_and_burn_subtitles`, LOCALIZE:
  ```python
      af = f"atempo={tempo}"
  ```
  Troque por (volume consistente entre clipes):
  ```python
      af = f"atempo={tempo},loudnorm=I=-14:TP=-1.0:LRA=11"
  ```
  Teste um clipe e ouça — o volume deve ficar parelho. Se preferir sem, reverta.

- [ ] **11.2 — Limpeza automática de `temp/`.** A pasta `temp/` acumula gigabytes. Crie um script `limpar_temp.sh` na raiz:
  ```bash
  #!/usr/bin/env bash
  # Apaga arquivos de temp/ com mais de 2 dias. NÃO toca em resultados/.
  find temp/ -type f -mtime +2 -delete 2>/dev/null
  find temp/ -type d -empty -delete 2>/dev/null
  echo "temp/ limpo."
  ```
  Rode `chmod +x limpar_temp.sh`. Use manualmente quando quiser. **Não** apague `resultados/`.

- [ ] **11.3 — SQLite WAL (web).** Só se você usa a interface web. Em `app/web/store.py`, na função que abre a conexão (`_connect`), logo após criar a conexão, adicione:
  ```python
      conn.execute("PRAGMA journal_mode=WAL")
  ```
  Teste subindo `web_main.py` e criando um job.

---

## FASE 12 — Postar no TikTok com menos cliques (semiautomático, SEM risco)

**Objetivo:** um botão na GUI que deixa tudo pronto pra postar — **copia a legenda**, **abre a pasta com o clipe** e **abre a página de upload do TikTok** no navegador. Você só arrasta o vídeo e cola a legenda (Ctrl+V) e clica publicar. O programa **NÃO** faz login nem publica sozinho → zero risco para a conta.
**Arquivo:** `gui.py` (e um item pequeno em `config.py`).
**Observação:** esta fase é **independente** das outras (só mexe na GUI); pode ser feita a qualquer momento. Reaproveite funções que já existem — não é preciso inventar muito código.

- [ ] **12.1 — Config da URL de upload.** No fim de `app/core/config.py`, crie uma variável de ambiente chamada `TIKTOK_UPLOAD_URL` com valor padrão `https://www.tiktok.com/tiktokstudio/upload` (assim, se o TikTok mudar o endereço, dá pra corrigir pelo `.env` sem mexer no código). Documente essa variável no `.env.example`.

- [ ] **12.2 — Criar o método `_post_to_tiktok_selected` na classe da GUI (`CortesApp`, em `gui.py`).** Ele deve fazer, nesta ordem, **reaproveitando código que já existe no arquivo**:
  1. Descobrir qual clipe está selecionado na tabela usando o método que **já existe**: `_selected_mp4_path()`. Se nada estiver selecionado, mostrar um aviso com `messagebox.showinfo(...)` (igual aos outros botões) e parar.
  2. Copiar a legenda para a área de transferência **usando a mesma lógica que já está em `_copy_caption_selected`** (ela lê o arquivo `.txt` que fica ao lado do `.mp4` e usa `self.clipboard_clear()` + `self.clipboard_append(...)`). Se não existir o `.txt`, seguir mesmo assim, só sem copiar legenda.
  3. Abrir a pasta onde está o clipe (pra facilitar arrastar) **usando a função de módulo que já existe `_open_folder(...)`** (por volta da linha 170), passando a pasta do arquivo (`Path(mp4).parent`).
  4. Abrir a página de upload do TikTok no navegador padrão **usando o módulo `webbrowser` da biblioteca padrão do Python** (`webbrowser.open(<valor de TIKTOK_UPLOAD_URL>)`). Adicione `import webbrowser` no topo do arquivo se ainda não houver.
  5. (Opcional) Mostrar uma mensagem curta: "Legenda copiada. Arraste o vídeo e cole a legenda com Ctrl+V."

  > ⚠️ **REGRA DE OURO desta fase:** este método **só abre e prepara**. Ele **NÃO** pode tentar logar, preencher formulário, ou clicar em "Publicar". É exatamente isso que mantém o risco em ZERO. Se você se pegar escrevendo automação de navegador (Selenium/Playwright), PARE — não é isso que foi pedido.

- [ ] **12.3 — Adicionar o botão na tabela de resultados.** Perto dos botões que já existem ("Copiar legenda (selecionado)" e "Copiar caminho (selecionado)", que ficam no frame chamado `r_a`, por volta da linha 925 de `gui.py`), adicione um botão novo com o texto **"Postar no TikTok (selecionado)"** que chama `self._post_to_tiktok_selected`. Use o **mesmo estilo** (`style=sec`) e o mesmo padrão de `.pack(...)` dos botões vizinhos, para ficar visualmente igual.

- [ ] **12.4 — Testar:** gere alguns clipes, selecione um na tabela e clique em **"Postar no TikTok (selecionado)"**. Deve acontecer: (a) abre a página de upload do TikTok no navegador, (b) abre a pasta com o clipe, (c) a legenda já cola com Ctrl+V. Complete o post arrastando o arquivo e colando a legenda. Rode `pytest` (essa mudança não tem teste automatizado — o teste é visual).

---

## FASE 13 — Verificação final

- [ ] **13.1 — Rodar todos os testes:**
  ```bash
  .venv/bin/python -m pytest -q
  ```
  Deve passar pelo menos o mesmo número de testes da Fase 0.2.

- [ ] **13.2 — Teste de ponta a ponta** com um vídeo real de ~1 min com 2 pessoas falando. Confira TODOS os ganhos juntos:
  - Transcrição rodou local (log).
  - Legenda com karaokê + contorno + fonte nova.
  - Enquadramento suave e focando quem fala.
  - Barra de progresso e cores no vídeo.
  - Encode rápido (NVENC).
  - Botão "Postar no TikTok" abre o navegador + a pasta e copia a legenda.

- [ ] **13.3 — Commit final:**
  ```bash
  git add -A && git commit -m "Melhorias: transcrição local, legenda karaokê, crop falante, NVENC, identidade visual, postar no TikTok"
  ```

---

## Mapa rápido "problema → onde mexer"

| Quero… | Fase | Arquivo principal |
|--------|------|-------------------|
| Transcrever mais rápido / por palavra | 1 | `ai_integrations/transcriber.py` + `local_whisper.py` |
| Legenda estilo TikTok | 2 | `subtitle/ass_builder.py`, `subtitle_burner.py` |
| Encode mais rápido (cortes) | 3 | `pipelines/cortes/pipeline.py`, `config.py` |
| Batalha/História usarem a GPU | 4 | `batalha_ffmpeg.py`, `historia_pipeline.py` |
| Enquadramento suave | 5 | `video_processing/focal_crop.py` |
| Focar quem fala / tela dividida | 6 | `video_processing/focal_crop.py`, `subtitle_burner.py` |
| Deixar o vídeo "com marca" | 7 | `video_processing/subtitle_burner.py` |
| Cortes melhores em vídeo longo | 8 | `ai_integrations/viral_analyzer.py` |
| Dublagem na GPU | 9 | `video_processing/tts_dubber.py` |
| Download mais rápido | 10 | `download/ytdlp_download.py` |
| Postar no TikTok com menos cliques | 12 | `gui.py` |
