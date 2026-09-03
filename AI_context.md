# Contexto completo do projeto — `meu_saas_cortes`

> Snapshot técnico consolidado em **03/09/2026** a partir do estado atual da pasta de trabalho.
> Este documento descreve o comportamento esperado e o comportamento efetivamente implementado
> no snapshot. Se uma implementação futura mudar uma regra importante, atualize este arquivo junto.

## 1. Resumo executivo

`meu_saas_cortes` é uma plataforma **local-first**, escrita em Python, para automatizar a criação
de vídeos verticais para TikTok, Reels, Shorts e YouTube Shorts. O nome conceitual do produto é
**SaaS de Cortes Virais**; ele não é um SaaS hospedado: não há login, multi-tenancy, billing ou
servidor remoto próprio. O programa roda na máquina do usuário e pode ser acessado por:

- CLI (`main.py`);
- GUI desktop em Tkinter (`gui.py`);
- interface web local em FastAPI (`web_main.py`);
- bot privado do Telegram (`telegram_bot.py`).

O núcleo mais antigo e mais importante é o gerador de **Cortes Virais**: recebe vídeos locais ou
URLs, transcreve, escolhe momentos, traduz, queima legendas em 9:16, gera uma descrição para o
post e opcionalmente dubla o áudio.

O mesmo executável agora também contém geradores de conteúdo independentes:

| Gerador/ferramenta | Entrada | Saída principal | Estado |
|---|---|---|---|
| Cortes Virais | vídeo longo, vários vídeos ou URL | vários MP4 verticais + `.txt` + manifest | implementado |
| Máquina de Quizzes | tema, quantidade, dificuldade, timer e voz | um MP4 vertical + `.txt` | implementado |
| Batalha 1v1 | tema, modo físico e voz | um MP4 vertical + `.txt` | implementado |
| História | texto longo e voz | um MP4 narrado vertical | implementado, requer ComfyUI local |
| Text-to-Speech | texto e voz | MP3 | implementado |
| Análise de desempenho | CSV de posts | três recomendações editoriais | implementado |
| Loop de retenção | JSON exportado do TikTok | `growth_profile.json` | implementado |
| Publicar no YouTube | cinco pares MP4/TXT | uploads privados agendados | implementado na GUI |

Regras de produto que não devem ser perdidas:

- saída de vídeo vertical, normalmente **1080×1920**, proporção 9:16;
- o corte principal gera por padrão **5 clipes de aproximadamente 50 segundos por bloco de 20 minutos**;
- legendas dos cortes são queimadas no MP4, não entregues apenas como arquivo externo;
- TikTok continua sendo um fluxo manual: o app prepara MP4 e descrição, abre o TikTok Studio e copia o texto,
  mas não publica automaticamente;
- conteúdo de terceiros baixado da internet continua sob responsabilidade do usuário quanto a direitos autorais,
  termos de uso e políticas das plataformas.

## 2. Estado da árvore e fonte de verdade

O documento representa a **working tree atual**, não necessariamente apenas o último commit. Há várias
alterações locais não commitadas e documentação histórica removida da árvore de trabalho. Em particular,
documentos antigos como `README.md`, `projeto.md`, `FLUXO_DE_DADOS.md` e o antigo `AI_CONTEXT.md` ainda
existem no histórico Git, mas não devem ser tratados como o estado atual.

O arquivo de configuração de ambiente é `.env.example`. Segredos ficam em `.env`, que é ignorado pelo Git.
Saídas, bancos locais, vídeos, áudios, caches e credenciais também são ignorados.

Commits recentes visíveis no snapshot:

- branch: `main`;
- HEAD observado: `ad3639c` — especificação de nomeação curta dos clipes;
- há alterações locais posteriores ao HEAD.

Quando este documento disser “atual”, significa o código presente na pasta no momento do snapshot.

## 3. Modelo mental da arquitetura

```text
CLI / GUI / Web / Telegram
          │
          ├── Cortes Virais ──> app/pipelines/cortes/pipeline.py::run_pipeline
          │                         │
          │                         ├── FFmpeg / ffprobe
          │                         ├── Whisper local ou Groq
          │                         ├── Groq/local LLM para momentos
          │                         ├── Google Translate
          │                         ├── MediaPipe/OpenCV para smart crop
          │                         ├── TTS opcional + mux
          │                         └── MP4 + TXT + manifest
          │
          ├── Quiz ───────────> app/pipelines/quiz/quiz_pipeline.py
          ├── Batalha ────────> app/pipelines/batalha/batalha_pipeline.py
          ├── História ───────> app/pipelines/historia/historia_pipeline.py
          └── TTS avulso ─────> app/tts/tts_standalone.py

Analytics e publicação são ferramentas laterais da GUI:

CSV/JSON de desempenho ──> app/analytics/* ──> recomendações/growth profile
MP4 + TXT ────────────────> app/publishing/* ──> YouTube Data API v3
```

O pipeline de cortes é compartilhado por todas as interfaces, mas os outros geradores são chamados
diretamente pela GUI ou pelo Telegram. A web local atualmente expõe apenas o fluxo de cortes.

## 4. Stack e dependências

### Python e bibliotecas

O projeto é executado com Python moderno; o `pyproject.toml` usa alvo Ruff `py312` e o ambiente observado
usa Python 3.12.3. Não há `setup.py`/`pyproject` de empacotamento: a aplicação é executada na raiz do
repositório.

Dependências de `requirements.txt`:

- `python-dotenv`: carrega `.env` na importação de `app.core.config`;
- `Pillow`, `opencv-python-headless`, `numpy`, `mediapipe`: imagens, smart crop e renderizações;
- `groq`: Groq Whisper e chat completions;
- `deep-translator`: tradução via Google Translate;
- `edge-tts`: voz e dublagem via Edge;
- `yt-dlp[default]`: downloads e buscas no YouTube/outros hosts;
- `sv-ttk`: compatibilidade visual usada pela GUI antiga/auxiliar;
- `fastapi`, `uvicorn`, `jinja2`, `python-multipart`: interface web;
- `redis`, `rq`: fila opcional da interface web;
- `python-telegram-bot`: bot Telegram;
- `pymunk`: física 2D da Batalha;
- `duckduckgo-search`: fallback de busca de imagens da Batalha;
- `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`: upload oficial ao YouTube.

`requirements-dev.txt` contém `pytest` e `ruff`.

`requirements-extra.txt` contém suporte opcional a `yt-dlp`/`secretstorage`; o `yt-dlp` principal já está
em `requirements.txt`, mas o extra documenta cenários de download fora do pipeline e cookies de navegador.

`requirements-local-tts.txt` contém `kokoro` e `soundfile`. PyTorch não fica nesse arquivo porque a instalação
GPU é feita separadamente pelo script `scripts/install_local_tts.sh`.

### Dependências de sistema e serviços

- **FFmpeg** e **ffprobe** são obrigatórios para processamento de vídeo/áudio.
- FFmpeg precisa ter o filtro `drawtext` para hook, CTA, watermark e overlays.
- GPU é opcional, mas acelera transcrição, smart crop, TTS local e encode.
- Node, Deno ou Bun são recomendados para resolver desafios JavaScript do YouTube via yt-dlp.
- Cookies de navegador/arquivo podem ser necessários para YouTube.
- História precisa de um ComfyUI local em `http://127.0.0.1:8188` com o workflow e custom nodes corretos.
- YouTube precisa de credencial OAuth de “Aplicativo para computador” e internet.
- Gemini, Groq, Edge-TTS, Google Translate, Wikimedia/Wikipedia/DuckDuckGo e YouTube são serviços externos;
  chamadas podem falhar, sofrer rate limit ou mudar de comportamento.

## 5. Instalação e comandos de operação

### Instalação base

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# editar .env e preencher pelo menos GROQ_API_KEY para os fluxos que usam LLM/Groq
```

`main.py`, `gui.py`, `web_main.py`, `web_worker.py` e `telegram_bot.py` chamam `_venv_reexec.ensure_venv()`.
Se encontrarem `.venv` na raiz, reiniciam o próprio processo usando esse Python. Também procuram um venv
alternativo em `~/.venvs/<nome-da-pasta>`.

Os entrypoints mudam o diretório de trabalho para a raiz quando necessário. Caminhos relativos como
`resultados/`, `temp/`, `assets/` e `workflow_historia.json` devem ser interpretados a partir da raiz.

### Executar Cortes pela CLI

```bash
.venv/bin/python main.py video.mp4
.venv/bin/python main.py video.mp4 --lang en
.venv/bin/python main.py video.mp4 --position top --font Arial \
  --color '#FFFFFF' --bg-color '#000000' --opacity 80
.venv/bin/python main.py video1.mp4 video2.mkv
.venv/bin/python main.py video.mp4 --dub-en
.venv/bin/python main.py video.mp4 --dub-pt --tts-voice pt-BR-FranciscaNeural
```

Argumentos CLI atuais:

| Argumento | Padrão | Regra |
|---|---:|---|
| `video` | obrigatório | um ou vários caminhos locais |
| `--lang` | `pt` | `pt` ou `en` |
| `--position` | `bottom` | `bottom` ou `top` |
| `--font` | `Arial` | o burner troca `Arial` pelo `TIKTOK_SUBTITLE_FONT` configurado |
| `--color` | `#FFFF00` | cor hexadecimal do texto/overlays |
| `--bg-color` | `#000000` | cor do fundo de legenda |
| `--opacity` | `75` | opacidade 0–100 |
| `--dub-en` | desligado | mutuamente exclusivo com `--dub-pt` |
| `--dub-pt` | desligado | mutuamente exclusivo com `--dub-en` |
| `--tts-voice` | vazio | voz passada ao dublador quando aplicável |

### Executar a GUI

```bash
.venv/bin/python gui.py
# Windows: abrir_gui.bat
```

A janela se chama **Cortes Lab — Creative Automation Studio**. A ação principal da GUI é Cortes Virais;
as ferramentas secundárias ficam na navegação lateral/abas.

### Executar a interface web local

```bash
.venv/bin/python web_main.py
# padrão: http://127.0.0.1:8765/
```

Para usar RQ/Redis em outro terminal:

```bash
# Redis deve estar rodando
REDIS_URL=redis://127.0.0.1:6379/0 .venv/bin/python web_worker.py
```

Sem `REDIS_URL`, a web executa jobs em threads daemon dentro do próprio processo do servidor. Isso é útil
para desenvolvimento local, mas não é uma fila durável de produção.

### Executar o bot Telegram

```bash
.venv/bin/python telegram_bot.py
```

Exige `TELEGRAM_BOT_TOKEN` e `TELEGRAM_ALLOWED_USER_ID`. O bot é privado: somente o ID configurado pode
usar os comandos.

### TTS local Kokoro

```bash
bash scripts/install_local_tts.sh
```

O script instala PyTorch com CUDA 12.8 e Kokoro. É direcionado principalmente a GPUs NVIDIA/RTX 50xx,
mas o código consegue usar CPU quando `LOCAL_TTS_DEVICE=cpu`.

### Testes e lint

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check .
```

Os testes são majoritariamente unitários e usam mocks. Eles não substituem um teste manual com vídeo real,
FFmpeg, rede, Groq, Edge-TTS, GPU ou ComfyUI.

## 6. Interface desktop: responsabilidades da GUI

`gui.py` é um arquivo grande que orquestra a experiência desktop; os cálculos e pipelines ficam em `app/`.
Todos os jobs longos rodam em thread para não congelar o Tkinter. stdout/stderr e logs são redirecionados
para uma fila consumida pelo painel de atividade.

As sete workspaces/abas são:

1. **Cortes Virais**
   - escolhe um ou mais vídeos locais;
   - busca por tema no YouTube;
   - aceita URLs diretas;
   - configura idioma, posição, fonte, cores e opacidade;
   - escolhe dublagem `off`/`en`/`pt` e voz;
   - toggles para transcrição local/Groq, karaokê, grade visual, barra, smart crop, GPU e Kokoro na dublagem;
   - botões para abrir resultados, cancelar, gerar ZIP, copiar legenda, abrir TikTok Studio e enviar Telegram.

2. **Máquina de Quizzes**
   - tema/nicho;
   - 1–10 perguntas na GUI;
   - dificuldade Fácil, Médio, Difícil ou Variado;
   - timer de 3–10 segundos;
   - voz TTS;
   - cor hexadecimal de fundo.

3. **Batalha 1v1**
   - tema;
   - modo Tamanho/Agar.io, Território ou Plinko/Corrida;
   - voz TTS.

4. **História**
   - texto longo;
   - voz TTS;
   - envia cenas ao ComfyUI local.

5. **Text-to-Speech**
   - texto;
   - catálogo de vozes local/Gemini/Edge;
   - prévia e geração de MP3.

6. **Publicar no YouTube**
   - escolhe exatamente 5 MP4 e os 5 TXT correspondentes;
   - configura horário, fuso e “feito para crianças”;
   - executa OAuth e agenda uploads.

7. **Análise de desempenho**
   - analisa CSV;
   - mostra três temas recomendados;
   - importa relatório JSON do TikTok para o loop de retenção.

O cancelamento desktop é cooperativo. `request_cancel()` sinaliza o cancelamento; subprocessos que usam
`run_cancelable()` são encerrados em pontos seguros. Não há garantia de interromper instantaneamente uma
requisição externa ou uma etapa atômica do FFmpeg.

## 7. Pipeline Cortes Virais — contrato completo

### Entrada pública

A função central é:

```text
run_pipeline(
    video_path,
    target_language='pt',
    posicao='bottom',
    fonte='Arial',
    cor_letra='#FFFF00',
    cor_fundo='#000000',
    opacidade=75,
    dub_to=None,
    tts_voice=None,
    progress=None,
    source_by_path=None,
    manual_start=None,
    manual_end=None,
    hook_text=None,
    outro_text=None,
) -> list[str]
```

`video_path` pode ser uma string, lista ou tupla. A lista de retorno contém apenas os caminhos dos MP4
finais, em ordem de prioridade (`1_`, `2_`, etc.). Arquivos `.txt` e manifests são efeitos colaterais,
não aparecem na lista de retorno.

`source_by_path` é um mapa de caminho absoluto para `VideoSourceAttribution`, usado para incluir crédito
do canal quando a entrada veio de yt-dlp.

### Ordem de execução

1. `run_pipeline` zera o cancelamento e cria `OUTPUT_DIR`/`TEMP_DIR`.
2. Cada entrada é validada via `ffprobe`.
3. Fontes acima de 20 minutos são divididas em blocos completos de 20 minutos.
4. Cada vídeo/bloco é preparado: fingerprint, transcrição e seleção de momentos.
5. Os clipes desse vídeo são processados em paralelo.
6. O resultado do vídeo é ordenado pelo índice original e um manifest é gravado.
7. Blocos temporários de vídeos longos são removidos no `finally`.

### Vídeos longos

`app/video_processing/video_splitter.py` usa `CHUNK_DURATION_SEC=1200`.

- vídeo até aproximadamente 20 minutos: segue intacto;
- vídeo maior: somente blocos completos de 20 minutos viram entradas;
- o resto incompleto é descartado deliberadamente;
- o split usa FFmpeg com `-c copy`, sem recodificação;
- um vídeo de 1 hora vira três blocos de 20 minutos;
- blocos recebem nomes temporários como `nome__parte_01.mp4`;
- a atribuição do canal é propagada para todos os blocos.

Isso é diferente dos scripts legados `baixarlinks.sh/.bat`, que segmentam downloads em partes de 30 minutos.
O comportamento oficial do pipeline é o bloco de 20 minutos.

### Fingerprint e cache

`app/core/cache.py` calcula fingerprint com tamanho, `mtime_ns` e SHA-256 do primeiro/último 1 MB. Para
arquivos pequenos, lê o conteúdo inteiro. O cache padrão é:

```text
Linux/macOS: ~/.cache/meu_saas_cortes/
Windows:     %LOCALAPPDATA%/meu_saas_cortes/
```

`CACHE_DIR` pode sobrescrever o diretório.

Namespaces atuais:

- `segments/`: transcrição;
- `moments/`: lista legada de momentos;
- `moment_analysis/`: seleção + candidatos + metadados;
- `translations/`: traduções por conteúdo de segmentos e idioma;
- `crop_plans/`: plano de smart crop.

As chaves são hashes determinísticos de JSON com ordenação de chaves. Os writes JSON são atômicos, usando
arquivo temporário com UUID e `replace`.

O cache de momentos inclui, entre outros, versão do analisador, idioma de saída, perfil de seleção,
quantidade de candidatos, perfil de desempenho e duração-alvo sugerida. Trocar o idioma ou o perfil pode
invalidar a análise de momentos.

### Preparação: transcrição

O contrato básico de segmento é:

```text
{
  "start": float,
  "end": float,
  "text": string
}
```

Transcrição local, quando realmente disponível, acrescenta:

```text
"words": [{"start": float, "end": float, "word": string}]
```

Com `TRANSCRIBE_BACKEND=local`, o código tenta `faster-whisper` somente se o pacote estiver instalado e
`torch.cuda.is_available()` for verdadeiro. Usa por padrão `large-v3`, `float16`, VAD e timestamps por
palavra. Se não houver GPU CUDA/pacote, cai automaticamente para Groq.

Com Groq, o áudio é enviado ao modelo `whisper-large-v3` em `verbose_json`. Áudio maior que aproximadamente
42 segundos é partido em fatias de aproximadamente 42 segundos; as chamadas usam `GROQ_TRANSCRIBE_MAX_WORKERS`
limitado por `GROQ_MAX_IN_FLIGHT`, e os timestamps são deslocados e ordenados. Se uma chamada única não
cobrir todo o áudio, o pipeline refaz em fatias.

Antes da transcrição Groq, o vídeo é convertido para MP3 mono 16 kHz, 32 kbps. Em fatias longas, quando
possível, a extração pode ser feita diretamente do vídeo.

### Preparação: seleção de momentos virais

`app/ai_integrations/viral_analyzer.py` faz uma chamada de descoberta com vários candidatos, não uma chamada
separada por candidato. O padrão é:

- `VIRAL_CLIPS_COUNT=5` selecionados;
- `VIRAL_CANDIDATE_COUNT=12`, limitado entre 10 e 20 e nunca menor que a quantidade final;
- no máximo aproximadamente 14.000 caracteres de transcrição no prompt;
- resposta esperada: JSON array;
- temperatura baixa e até três tentativas de parse;
- versão atual do analisador: `weighted_candidates_v4_groq_reasoning`.

O prompt atual é fortemente orientado a guitarra, música, rock, blues, teoria musical e entrevistas com
músicos. Para outros nichos, a infraestrutura funciona, mas essa orientação pode enviesar a escolha.

Cada candidato pode conter:

```text
start, end, reason, hook, cta,
category, topic, entities,
hook_strength, standalone_clarity, curiosity, controversy,
emotional_strength, practical_value, shareability, comment_potential,
general_audience, niche_relevance, famous_person_or_topic,
story_progression, ending_payoff, retention_likelihood,
needs_previous_context, slow_start, too_technical, repetitive,
weak_ending, filler, generic_advice, incomplete_thought
```

Categorias válidas:

- `broad_appeal`;
- `controversy_opinion`;
- `curiosity`;
- `practical_value`;
- `niche_hardcore`.

O ranking é determinístico e local:

- converte scores para 0–10;
- aplica pesos de `balanced`, `tiktok_growth` ou `educational`;
- subtrai penalidades de contexto anterior, começo lento, repetição e final fraco;
- pode ajustar levemente pelo perfil histórico de desempenho;
- refina o início para um limite natural de fala, com até 2 segundos de pré-roll;
- procura um fim próximo de uma frase concluída, em uma janela de aproximadamente ±7 segundos;
- remove sobreposição maior que 0,25 s;
- remove duplicatas semânticas com similaridade aproximada de 0,60;
- tenta cobrir categorias diferentes na ordem broad, controversy, curiosity, practical e niche;
- marca posição de ranking, posição selecionada e motivo de descarte nos candidatos.

Se o JSON for inválido após três tentativas, ou se vierem menos candidatos utilizáveis, cria janelas temporais
de fallback distribuídas ao longo da transcrição. O pipeline continua produzindo saída sempre que houver
segmentos suficientes.

### Corte manual

Quando `manual_start` e `manual_end` são fornecidos juntos:

- ambos devem ser finitos;
- `start >= 0`;
- `end > start`;
- duração mínima de 4 segundos;
- `end` é limitado ao fim da transcrição;
- a análise viral é pulada;
- é criado um único momento com metadados fixos e hook opcional.

Esse recurso está exposto na web como `clip_start`/`clip_end`. A GUI desktop atual não exibe campos de corte
manual; o Telegram também não expõe essa opção.

### Processamento de cada clipe

Para cada momento selecionado:

1. `app/pipelines/cortes/pipeline.py` coleta segmentos que **intersectam** a janela, inclusive aqueles
   parcialmente cortados na borda. O texto parcial é aproximado por palavras para evitar legendas que pulem
   o começo/fim de uma frase.
2. Traduz os segmentos para `pt` ou `en` com `deep-translator`/Google Translate. O padrão atual permite
   batching; se o delimitador não voltar com a mesma quantidade de partes ou houver resposta de erro, volta
   ao modo segmento a segmento. Falha de tradução preserva o texto original.
3. Gera SRT relativo ao início do clipe, aplicando `playback_speed = 1 + CLIP_SPEED_UP_PERCENT/100`.
4. O SRT limpa repetições, limita cada legenda a 160 caracteres, no máximo duas linhas e cerca de 44 caracteres
   por linha.
5. Em paralelo ao encode, pede uma descrição de post TikTok ao LLM rápido.
6. Executa um único passe principal de FFmpeg para corte, aceleração, escala/crop, legenda ASS, hook, CTA,
   grade, barra e watermark.
7. Se houver dublagem, gera áudio traduzido, encaixa os trechos nos slots e faz mux.
8. Salva o `.txt` ao lado do MP4 e limpa sidecars `.srt`, `.ass` e arquivos auxiliares.

O pipeline não deve voltar a criar um MP4 intermediário apenas para depois queimar legendas: o contrato atual
é `cut_and_burn_subtitles`, que corta e renderiza a camada visual no mesmo passe. Dublagem naturalmente usa
intermediários de áudio e mux depois.

### Legendas e identidade visual dos cortes

`app/video_processing/subtitle_burner.py` usa ASS com resolução lógica 1080×1920 e margem elevada para não
cobrir o nome do perfil, legenda nativa e botões do TikTok.

Com `SUBTITLE_KARAOKE=1` (padrão):

- palavra já falada fica na cor de destaque `SUBTITLE_KARAOKE_HIGHLIGHT`;
- texto restante fica branco;
- o tempo das palavras é distribuído igualmente pela duração da linha, mesmo sem timestamps por palavra.

Com karaokê desligado, usa cor de texto e fundo/opacidade configurados.

Elementos temporizados:

- hook visual no topo: visível até antes de 3 segundos;
- CTA contextual: normalmente entre 13 e 15 segundos;
- grade opcional: contraste, saturação, brilho e vinheta;
- barra de progresso opcional no topo;
- watermark opcional no canto inferior direito;
- tela final opcional quando `outro_text` é preenchido.

O cartão de outro é renderizado depois do clipe principal, em CPU, com branding textual `BENDIFY`. A web já
vem com um hook e um texto de outro preenchidos; a GUI não mostra esses campos no workspace de Cortes.

Nuance importante: o momento normalizado sempre carrega a chave `cta`. Se ela vier explicitamente vazia,
isso suprime o CTA genérico do burner; se o burner receber `None`, ele pode usar o CTA “seguir o perfil”.

### Smart crop

`app/video_processing/focal_crop.py` tenta manter pessoas visíveis ao adaptar vídeos horizontais para 9:16.

- o modelo `blaze_face_full_range.tflite` é baixado sob demanda para o cache do usuário;
- sem OpenCV, sem modelo, sem detector ou sem detecção confiável, cai para crop central;
- com um rosto, calcula crop estático a partir do rosto maior/mediano;
- com dois ou mais rostos, estima o falante pela energia de movimento na região da boca;
- usa amostragem, EMA, histerese e intervalo mínimo entre mudanças para evitar saltos;
- usa `silencedetect` para evitar trocar o foco durante silêncio quando o vídeo fornece essa informação;
- quando dois rostos ficam afastados em mais de 35% da largura e `SMART_CROP_SPLIT_ENABLED=1`, retorna modo
  `split`: rosto esquerdo na metade superior e rosto direito na metade inferior;
- o modo split usa `filter_complex`/`vstack` e é sempre codificado em CPU, inclusive quando GPU está ativa;
- planos são cacheados com resolução, amostras, FPS de falante, intervalo mínimo e, para clipes, início/fim.

No Linux, o delegate GPU do MediaPipe é usado por padrão apenas quando há driver NVIDIA detectável.
Em AMD/Mesa o padrão é CPU; `SMART_CROP_MEDIAPIPE_GPU_FORCE=1` força uma tentativa que pode falhar.

### Dublagem

`dub_to` aceita `en` ou `pt`. A dublagem:

- traduz o texto dos segmentos para o idioma de destino;
- calcula o tempo relativo após o speed-up;
- gera uma síntese por segmento;
- acelera com uma cadeia `atempo` quando o TTS é maior que seu slot, até `DUB_MAX_TTS_SPEEDUP`;
- corta o excedente se ainda necessário;
- posiciona cada trecho com `adelay` e combina os trechos com `amix`;
- substitui o áudio original, mantendo o vídeo renderizado;
- usa AAC copy quando os codecs permitem, caso contrário reencoda para AAC.

O dublador tenta Kokoro local quando `LOCAL_TTS_PREFERRED=1` e o pacote está disponível; nessa rota usa a
voz local configurada, não necessariamente o identificador Edge passado pelo usuário. Caso contrário usa
Edge-TTS com `EDGE_TTS_VOICE` ou `EDGE_TTS_VOICE_PT`.

Com `DUB_TRIM_SILENCE=1`, o pipeline detecta silêncios longos e concatena apenas partes faladas. Isso pode
gerar pequenos “pulos” visuais; desligue para priorizar continuidade do quadro.

### Descrição para o post TikTok

`app/ai_integrations/tiktok_caption.py` não gera a legenda queimada; gera o texto da descrição do post.

Contrato atual:

- até 15 palavras na linha principal;
- sem emojis;
- entre 3 e 5 hashtags específicas do assunto;
- hashtags genéricas de FYP/viral são filtradas;
- fallback usa o começo da transcrição e palavras-chave reconhecidas;
- links são removidos antes de gravar o `.txt`.

Se a origem veio de yt-dlp e `CAPTION_SOURCE_ATTRIBUTION=1`, o arquivo pode incluir uma linha como
`Review original: <canal>` ou `Original review: <canal>`.

### Progresso

O callback recebe um valor entre 0 e 1. Os marcos não são ETA real:

| Faixa aproximada | Etapa |
|---:|---|
| 0.00–0.02 | início |
| 0.02–0.05 | preparação/áudio |
| 0.12–0.48 | transcrição |
| 0.50–0.58 | análise de momentos |
| 0.58–1.00 | clipes |

Com vários vídeos, o progresso é escalado globalmente. O encode individual não fornece progresso exato;
o avanço da faixa de clipes é baseado na quantidade de tarefas concluídas.

### Paralelismo e GPU

- clipes usam `ThreadPoolExecutor` limitado por `PIPELINE_MAX_WORKERS` ou por fração de CPU;
- semáforos globais limitam encodes CPU/GPU;
- Groq e tradução têm semáforos próprios;
- dublagem Edge-TTS limita conexões simultâneas;
- o encode GPU é escolhido automaticamente no Linux quando não há override:
  - NVIDIA com encoder listado: `h264_nvenc`;
  - VA-API disponível: `h264_vaapi`;
  - fallback AMD/Windows pode ser `h264_amf`;
  - Intel pode ser configurado com `h264_qsv`;
- falha em encode de cortes/quiz tenta novamente em CPU;
- VA-API escolhe por padrão o `renderD*` de maior índice quando existem vários nós.

## 8. Nomenclatura e arquivos de saída

### Cortes

O nome oficial atual é:

```text
resultados/{indice}_{stem_sanitizado}.mp4
resultados/{indice}_{stem_sanitizado}.txt
```

O índice começa em 1. `sanitize_clip_output_stem` remove caracteres inválidos, colapsa underscores,
remove pontos/espaços nas bordas e limita o stem a 160 caracteres.

Exemplo:

```text
resultados/1_podcast_ep_42.mp4
resultados/1_podcast_ep_42.txt
resultados/2_podcast_ep_42.mp4
```

Em uma fila com stems repetidos, o pipeline cria nomes lógicos `stem`, `stem__2`, etc. Para fonte longa,
o stem inclui `__parte_01`.

Não usar a nomenclatura legada `*_viral_1.mp4`; ela aparece em documentação antiga, mas não é o contrato atual.

### Manifest de Cortes

```text
resultados/{video_name}__run_manifest_{YYYYMMDD_HHMMSS}.json
```

Inclui:

- caminho da fonte;
- nome lógico;
- fingerprint;
- opções recebidas;
- flags de cache;
- momentos selecionados;
- bloco de seleção com perfil, candidatos e explicações;
- lista de outputs;
- timestamp.

### Quiz

```text
resultados/quiz_{tema_sanitizado}_{YYYYMMDD_HHMMSS}.mp4
resultados/quiz_{tema_sanitizado}_{YYYYMMDD_HHMMSS}.txt
temp/quiz_{YYYYMMDD_HHMMSS}/...
```

### Batalha

```text
resultados/batalha_{tema_sanitizado}_{YYYYMMDD_HHMMSS}.mp4
resultados/batalha_{tema_sanitizado}_{YYYYMMDD_HHMMSS}.txt
temp/batalha_{YYYYMMDD_HHMMSS}/...
```

### História

```text
resultados/historias/historia_final_{YYYYMMDD_HHMMSS}.mp4
temp/historia_{YYYYMMDD_HHMMSS}/...
```

História não gera atualmente o `.txt` de legenda TikTok.

### TTS

```text
resultados/tts/{YYYYMMDD_HHMMSS}_{slug_do_texto}.mp3
temp/tts_preview/preview_{provider_voice}.mp3
```

### ZIP da GUI/web

```text
resultados/cortes_export_{YYYYMMDD_HHMMSS}.zip
```

O ZIP inclui MP4s, TXT correspondentes e `LEIA-ME_POSTAGEM.txt`. Não inclui manifest por padrão, a menos
que ele seja selecionado como arquivo por algum fluxo externo.

## 9. Máquina de Quizzes

Arquivos centrais:

- `app/pipelines/quiz/quiz_pipeline.py`: orquestração, prompts, áudio e FFmpeg;
- `app/pipelines/quiz/quiz_frames.py`: frames estáticos com Pillow.

### Contrato de pergunta

```text
{
  "pergunta": string,           # até 120 caracteres
  "opcoes": [string, string, string, string],  # cada uma até 35
  "resposta_correta": 0|1|2|3,
  "curiosidade_extra": string   # até 150 caracteres
}
```

O pipeline aceita payload com aliases em português/inglês (`theme`/`tema`, `count`/`quantidade`,
`timer_sec`/`timer`, `tts_voice`/`voice`, `difficulty`/`dificuldade`, `cor_fundo`/`bg_color`/`quiz_bg_color`).

### Fluxo

1. **LLM:** Groq/local LLM gera JSON com o número de perguntas pedido.
2. **Verificação factual:** uma segunda passagem de LLM confirma índice, resposta, fatos atuais, opções e
   curiosidade. Tenta até duas vezes; se falhar, mantém a geração inicial.
3. **Gancho de abertura:** uma chamada separada gera `gancho_abertura` e `subtitulo`, sem transformar o
   gancho em uma pergunta duplicada. Há fallback fixo `90% erram a pergunta 2!`.
4. **TTS:** para cada pergunta gera três faixas:
   - pergunta: somente o enunciado;
   - timer: ticks por segundo, criado por FFmpeg;
   - resposta: letra/texto da correta + curiosidade.
   Também gera o encerramento fixo: `E aí, foi bem? Comenta quantas você acertou.`
5. **Pillow:** cria frame de pergunta, frame de resposta, frame de hook, recompensas e outro.
6. **FFmpeg:** cada pergunta vira um segmento com três fases e depois todos os segmentos são concatenados.

Cada pergunta tem:

- fase 1: pergunta + quatro opções pelo tempo do TTS da pergunta;
- fase 2: timer de 3–10 segundos, com contagem visual 5→1 ou equivalente;
- fase 3: resposta revelada, opção correta verde, demais opções discretas, flash e shake curtos;
- cabeçalho `Pergunta N/Total` e barra de progresso;
- efeito `ding` opcional no começo da resposta.

Entre perguntas há um frame de aproximadamente 1 segundo com `Acertou?`/`Errou?`. No começo há um cartão
de gancho de 2,5–3 segundos. No fim há cartão e TTS de encerramento.

O encode tenta GPU com fallback CPU. A concatenação final usa demuxer FFmpeg e `-c copy`.

### Dificuldade

- `facil`: trivia conhecida, distratores claramente mais fracos;
- `medio`: conhecimento geral com alguma reflexão;
- `dificil`: fatos de segunda camada, datas/estatísticas/termos precisos e distratores plausíveis;
- `variado`: mistura de fácil, médio e difícil.

Prompts de geração e verificação ancoram fatos atuais no ano civil corrente e proíbem inventar nomes,
estatísticas ou recordes desatualizados.

### Estado dos assets do snapshot

O código referencia:

- `assets/ticking_5s.mp3`;
- `assets/ding.mp3`.

Esses arquivos não estão presentes na árvore atual. O timer gera um tom sintético quando o asset de ticking
falta; sem `ding.mp3`, a revelação segue sem ding e as micro-recompensas ficam sem som.

## 10. Batalha 1v1

Arquivos centrais:

- `batalha_pipeline.py`: Groq, assets, TTS e orquestração;
- `batalha_images.py`: logos/avatares;
- `batalha_frames.py`: física, PIL e modos;
- `batalha_ffmpeg.py`: stdin rawvideo, SFX e mux.

### Spec gerada

```text
{
  "oponente_1": string,
  "oponente_2": string,
  "termo_busca_1": string,
  "termo_busca_2": string,
  "cor_1": "#RRGGBB",
  "cor_2": "#RRGGBB",
  "hook": string,
  "script_narracao": string,   # normalizado para 50–60 palavras
  "legenda_tiktok": string
}
```

Se o tema contiver `vs`, `versus`, `x` ou `×`, o código extrai os dois oponentes e força termos de busca
de logo a partir deles. Caso contrário, o LLM escolhe o duelo.

Modos aceitos:

- `tamanho` (default; alias `agar`, `size`);
- `territorio` (alias `territory`);
- `plinko` (alias `race`, `corrida`).

### Assets de imagem

A busca tenta, em ordem prática:

1. Wikimedia Commons, priorizando imagens com “logo”;
2. thumbnail/resumo da Wikipedia;
3. DuckDuckGo como fallback.

O resultado é convertido em avatar circular para as bolinhas e logo retangular para a tela de vitória.
Se tudo falhar, usa avatar com a inicial do nome e a cor do oponente. Avatares/logos baixados são removidos
ao fim do pipeline; outros artefatos de trabalho podem permanecer em `temp/`.

### Física

- canvas 1080×1920;
- 30 FPS;
- duração máxima da simulação: 42 segundos;
- colisões registradas para SFX com debounce;
- frames são convertidos para RGB24 e enviados por stdin ao FFmpeg;
- a simulação não acumula todos os frames em RAM no fluxo normal.

**Tamanho/Agar:** duas bolas colidem; a maior ganha massa/raio e a menor encolhe. Quando uma chega ao
limite mínimo, a outra vence.

**Território:** colisões pintam uma grade de território. A vitória ocorre quando um jogador alcança a
proporção configurada, aproximadamente 88%.

**Plinko/Corrida:**

- pinos e cinco cestos com valores `10, 50, 100, 50, 10`;
- cinco bolinhas por time;
- um par é lançado a cada 2 segundos;
- arena não tem teto, para permitir entrada livre;
- divisórias entre cestos são finas para não criar um “teto” físico;
- a pontuação acontece quando a bola entra na zona do cesto, sem precisar tocar o chão;
- a física continua livre após pontuar;
- após todas as bolas pontuarem, há aproximadamente 0,45 s de placar;
- depois aparece a tela final com “Vitória do …” e o logo retangular do vencedor;
- TTS de vitória é pré-gerado para ambos os oponentes, mas somente o vencedor é usado.

### Áudio e saída

O vídeo mudo é codificado primeiro. Depois o mux coloca:

- TTS do hook no início;
- TTS da narração de 50–60 palavras depois do hook;
- em Plinko, TTS da vitória no timestamp da tela final;
- impactos de colisão com `adelay` + `amix`.

O código referencia `assets/ball.mp3` e usa `assets/ding.mp3` como fallback. Esses assets não existem no
snapshot; quando ambos faltam, cria um impacto sintético via lavfi.

## 11. História com ComfyUI

Arquivos:

- `app/pipelines/historia/historia_pipeline.py`;
- `app/pipelines/historia/comfyui_client.py`;
- `workflow_historia.json`.

### Fluxo

1. Groq/local LLM divide o texto em no máximo 5 cenas.
2. Cada cena contém narração em pt-BR e `prompt_visual` em inglês.
3. O prompt exige consistência de personagens, locais, figurino, iluminação e composição 9:16.
4. Cada narração vira MP3 pela camada TTS unificada.
5. Cada prompt visual gera vídeo pelo ComfyUI, salvo em `temp/`.
6. Se um prompt for vazio, reutiliza o último prompt válido/fallback.
7. Se o mesmo prompt reaparecer, reutiliza o vídeo bruto anterior e faz loop até a narração acabar.
8. Cada cena é escalada para 1080×1920 e muxada com seu áudio.
9. As cenas prontas são concatenadas com `-c copy`.

O número de vídeos gerados é limitado a 5, embora cenas excedentes possam ser fundidas em blocos.
O resultado é `resultados/historias/historia_final_<timestamp>.mp4`.

### ComfyUI

O cliente usa apenas biblioteca padrão para:

- carregar `workflow_historia.json`;
- injetar prompt no nó `4`;
- gerar a mesma seed nos nós `6` e `13`;
- forçar latent 512×896 no nó `1`;
- forçar 3 FPS no nó `8`;
- fazer POST em `/prompt`;
- consultar `/history/{prompt_id}` a cada aproximadamente 1,75 s, com timeout de 900 s;
- extrair `gifs`/`images` do nó `8`;
- baixar o arquivo via `/view`.

O workflow atual usa checkpoint `v1-5-pruned-emaonly.ckpt` e nós do Impact Pack/Impact Subpack para
detalhamento de rosto. O ComfyUI precisa ter esses custom nodes e o modelo `bbox/face_yolov8m.pt`.

### Atenções do snapshot

- `workflow_historia.json` está na raiz do repositório.
- O helper `_repo_root()` em `comfyui_client.py` sobe apenas dois níveis a partir de
  `app/pipelines/historia/comfyui_client.py`, o que resolve para `app/pipelines`, não para a raiz. Se
  História informar “workflow não encontrado”, a primeira verificação deve ser esse caminho.
- O mux de História usa diretamente `gpu_clip_encoder_ffmpeg_args()`; diferentemente do burner principal,
  não há uma camada tão completa de fallback CPU.
- A limpeza remove MP3s, vídeos brutos e `lista.txt`, mas cenas prontas podem permanecer na pasta temporária.
- História não chama o gerador de legenda TikTok e não cria `.txt` atualmente.

## 12. TTS unificado

Arquivos:

- `app/tts/tts_engine.py`: dispatch;
- `app/tts/tts_voices.py`: catálogo e resolução;
- `app/tts/local_tts.py`: Kokoro;
- `app/tts/gemini_tts.py`: REST Gemini;
- `app/video_processing/tts_dubber.py`: Edge-TTS e alinhamento;
- `app/tts/tts_standalone.py`: MP3 avulso/prévia.

### Identificadores de voz

O formato preferencial é:

- `local:pf_dora`;
- `gemini:Achernar`;
- `edge:pt-BR-FranciscaNeural`.

Também são aceitos nomes Edge legados sem prefixo.

Precedência de catálogo:

1. Kokoro local, se instalado e `LOCAL_TTS_PREFERRED=1`;
2. vozes Gemini, se `GEMINI_API_KEY` estiver configurada;
3. vozes Edge.

Vozes locais pt-BR:

- `pf_dora`;
- `pf_sara`;
- `pm_alex`;
- `pm_santa`.

Gemini inclui Achernar, Leda, Vindemiatrix, Despina, Aoede e Gacrux. Edge inclui Thalita, Francisca,
Antonio, Donato, Aria, Guy e Sonia, com deduplicação de IDs.

### Kokoro

Usa `hexgrad/Kokoro-82M`, `lang_code='p'`, sample rate 24 kHz, e converte WAV para MP3 com FFmpeg.
`LOCAL_TTS_DEVICE=auto` escolhe CUDA quando disponível, senão CPU. O singleton do modelo é protegido por
lock para evitar carregar múltiplas cópias.

### Gemini

Usa `generateContent` com `responseModalities=["AUDIO"]`, extrai PCM inline em camelCase ou snake_case,
converte PCM s16le mono 24 kHz para MP3 e tenta modelos TTS alternativos. Se falhar e a chamada permitir,
`tts_engine` cai para Edge-TTS.

### Edge

Cada síntese possui timeout, retentativas e backoff para 403/handshake. O limite padrão é duas conexões
simultâneas, para evitar falhas da sessão WebSocket do Edge.

## 13. Download por URL e histórico de fontes

`app/download/ytdlp_download.py` centraliza downloads, busca por tema, cookies, runtime JS e atribuição.

### URLs diretas

`collect_urls_from_lines` aceita linhas HTTP/HTTPS, `www.*`, `youtu.be` e YouTube sem esquema; ignora
comentários e linhas inválidas; remove duplicatas por identidade canônica.

`resolve_ytdlp_cmd` tenta, nesta ordem prática:

1. `YTDLP_PATH`/`YT_DLP_PATH`;
2. executável ao lado do Python atual;
3. `python -m yt_dlp`;
4. `yt-dlp` no PATH.

Cada opção é validada com `--version`, o que evita usar scripts com shebang quebrado após mover o projeto.

Para YouTube, o downloader tenta estratégias alternativas de `player_client`, usa runtime JS detectado e
componentes remotos EJS. Cookies podem vir de `YTDLP_COOKIES_FROM_BROWSER` ou `YTDLP_COOKIES_FILE`.

O formato prefere H.264 quando possível e mescla vídeo/áudio para MP4. O número de fragmentos paralelos é
`YTDLP_CONCURRENT_FRAGMENTS`.

### Busca por tema

`search_youtube_top_by_views` executa `ytsearchN`, padrão 20, filtra duração mínima de 600 s e ranqueia por:

- relevância de tokens do tema;
- presença de termos de formato falado, como podcast/entrevista/conversa;
- duração;
- entidade/tópico reconhecível;
- views em escala logarítmica.

Não escolhe apenas o maior número bruto de views se um resultado falado e relevante for melhor. Também exclui
fontes já usadas pelo histórico.

### SQLite de histórico

`app/core/source_history.py` usa `data/source_history.sqlite` por padrão. A tabela de fontes registra:

- `source_key` canônica;
- URL original;
- status `claimed`, `downloaded` ou `failed`;
- caminho baixado;
- canal;
- timestamps e erro.

URLs equivalentes do YouTube viram `youtube:<video_id>`. Outras URLs viram `url:<scheme+host+path+query>`
com parâmetros de rastreamento removidos.

O `claim` é transacional e impede dois downloads simultâneos da mesma fonte. Downloads concluídos são
reutilizados se o arquivo ainda existir; se o arquivo sumiu, a fonte pode ser baixada novamente. Falhas
ficam liberadas para retry.

## 14. Interface web local e API

### Componentes

- `web_main.py`: sobe FastAPI em `WEB_HOST`/`WEB_PORT`, padrão `127.0.0.1:8765`;
- `app/web/app.py`: factory, templates, estáticos e routers;
- `app/web/store.py`: SQLite da playlist;
- `app/web/queue_backend.py`: RQ/Redis ou thread local;
- `app/web/worker.py`: download + `run_pipeline` + progresso;
- `app/web/tasks.py`: funções enfileiradas;
- `app/web/hub.py`: snapshot e fan-out SSE;
- `app/web/templates/index.html`: formulário e tabelas;
- `app/web/static/js/app.js`: fetch, SSE e polling.

### Estados da playlist

Cada item possui dois estados independentes:

`workflow_status`:

- `pendente`;
- `publicado`;
- `descartado`.

`pipeline_status`:

- `idle`;
- `queued`;
- `running`;
- `done`;
- `error`.

Só é permitido marcar `publicado`/`descartado` depois que o pipeline terminou em `done`. Itens com erro
podem voltar a ser processados.

Banco padrão: `data/web_jobs.sqlite`. Acesso é protegido por lock Python e SQLite em WAL.

### Endpoints

| Método/rota | Função |
|---|---|
| `GET /` | página HTML |
| `POST /api/jobs` | job avulso ou lote de URLs/uploads |
| `GET /api/playlist` | lista itens, opcionalmente filtra workflow |
| `POST /api/playlist` | adiciona URLs/arquivos sem iniciar |
| `POST /api/playlist/process` | enfileira todos ou IDs específicos |
| `PATCH /api/playlist/{id}` | muda `pendente/publicado/descartado` |
| `DELETE /api/playlist/{id}` | remove registro, sem apagar automaticamente o vídeo de saída |
| `GET /api/playlist/active` | progresso agregado persistido no SQLite |
| `GET /api/progress` | SSE de progresso/log/status |
| `GET /api/progress/snapshot` | snapshot atual do `ProgressHub` |
| `GET /api/runs` | últimos MP4s em `OUTPUT_DIR`, com duração via ffprobe |

`POST /api/jobs` aceita URLs, arquivos, idioma, posição, dublagem, voz, ZIP, hook, outro e faixa manual.
O frontend atual envia principalmente URL/arquivos, idioma, posição, dublagem, ZIP e edição editorial.

Uploads são salvos em `temp/web_<stem>_<uuid>.<ext>`.

### Filas e progresso

Com Redis configurado, RQ enfileira jobs com timeout de 6 horas e o worker externo executa a tarefa. Sem
Redis, `enqueue_callable` cria uma thread daemon no processo web.

O `ProgressHub` é singleton apenas dentro de um processo. Quando o RQ roda em processo separado, o frontend
usa `/api/playlist/active` e os campos persistidos no SQLite para acompanhar o item; não se deve presumir
que o hub em memória do worker seja o mesmo do servidor HTTP.

A web não tem autenticação. O padrão `127.0.0.1` é intencional; expor a porta em rede exige camada externa
de segurança.

## 15. Bot Telegram

`telegram_bot.py` usa `python-telegram-bot`, mantém um `_job_lock` para permitir um job por vez e atualiza
uma mensagem de status a partir de uma fila de logs.

Comandos:

| Comando | Uso |
|---|---|
| `/start` ou `/help` | ajuda |
| `/cortes` | seguido de URLs e/ou caminhos locais, um por linha |
| `/tema assunto` | busca vídeo longo no YouTube e gera cortes |
| `/quiz tema [quantidade] [timer_sec]` | quiz; quantidade e timer são limitados a 1–10 e 3–10 |
| `/batalha [modo] tema` | Batalha Tamanho/Território/Plinko |
| `/historia texto` | História; requer ComfyUI |
| `/tts texto` | MP3 com voz padrão |

No Telegram, o quiz força temporariamente `EDGE_TTS_MAX_CONCURRENT=1` para reduzir 403/handshake.
O bot usa a voz padrão, não expõe todos os controles da GUI e não possui comando de cancelamento.

Ao terminar cortes/quiz/batalha/história, envia MP4 e usa o `.txt` correspondente como caption quando há
espaço. Vídeos acima de 50 MB não são enviados; nesse caso informa o caminho local e pode enviar o texto.
MP3 é enviado como áudio.

## 16. Analytics e feedback loop

### Análise de desempenho CSV

`app/analytics/performance.py` aceita CSV com encoding UTF-8/BOM, CP1252 ou Latin-1 e tenta detectar
delimitador entre vírgula, ponto e vírgula, tab e `|`.

Ele reconhece aliases em português/inglês para:

- tema/tópico/nicho/categoria;
- título/legenda/descrição/conteúdo;
- views, alcance ou impressões;
- likes, comentários, compartilhamentos, saves;
- seguidores;
- conclusão/retenção;
- tempo médio assistido;
- duração;
- data.

Exige uma coluna de conteúdo/tema e pelo menos uma métrica de exposição. Linhas sem conteúdo ou exposição
positiva são ignoradas.

O score local combina percentis:

- exposição: 35%;
- engajamento ponderado: 35% quando disponível;
- retenção/conclusão: 20% quando disponível;
- seguidores por exposição: 10% quando disponível.

Se `use_ai=True`, os 20 melhores sinais são enviados ao modelo rápido para devolver exatamente três temas
distintos. Se a síntese falhar, recomendações locais continuam disponíveis.

### Perfil de conteúdo do TikTok

`app/analytics/content_profile.py` reduz um relatório JSON para um objeto pequeno e determinístico que pode
entrar no prompt de seleção viral. Aceita lista ou chaves `videos`, `rows`, `items` ou `data`.

Ele procura texto em descrição/caption/título/conteúdo, métricas em `current_metrics` ou na raiz, identifica
tópicos, entidades, padrões amplos/técnicos e buckets de duração. Usa percentis de views, engajamento e share
rate para separar melhores e piores.

O perfil é apenas um sinal secundário. O prompt explicita que correlação não é causalidade e que horário,
watch time, tráfego ou seguidores não devem ser inventados quando não estão no relatório.

Pode ser ativado por `TIKTOK_PERFORMANCE_REPORT_PATH`.

### Loop de retenção

`app/analytics/retention_loop.py` espera um JSON de posts com, quando disponíveis:

- views e engajamento;
- `duration_bucket` ou duração;
- dia/horário de publicação;
- flags de pergunta, exclamação, emojis e quantidade de hashtags;
- seguidores antes/depois;
- hashtags/descrição para tópicos.

Exige no mínimo três vídeos com views. Produz:

- sinais por bucket de duração;
- melhores janelas de publicação com grupos de pelo menos três amostras;
- lift de recursos de legenda;
- ímãs de seguidores;
- duração recomendada.

Salva `data/growth_profile.json` com `schema_version=1`.

O pipeline de cortes lê `recommended_clip_duration_sec` como duração-alvo somente quando `CLIP_DURATION` não
foi explicitamente definido no ambiente. Como `.env.example` já contém `CLIP_DURATION=50`, copiar esse valor
para `.env` marca a duração como explícita e impede que o loop altere a meta.

## 17. Publicação oficial no YouTube

Arquivos:

- `app/publishing/youtube_schedule.py`;
- `app/publishing/youtube_uploader.py`;
- controles na aba “Publicar no YouTube” de `gui.py`.

O fluxo exige exatamente 5 MP4 e 5 TXT com os mesmos nomes-base. Os vídeos são ordenados naturalmente
(`1_`, `2_`, `10_` em ordem numérica), e cada MP4 é pareado com seu TXT.

Horário:

- aceita `7`, `7:00`, `07:30` e `7h30`;
- padrão `07:00`;
- padrão de fuso `America/Campo_Grande`, sobrescrito por `YOUTUBE_SCHEDULE_TIMEZONE`;
- começa amanhã e agenda uma publicação por dia;
- `youtube_publish_at` converte para RFC 3339 UTC.

Credenciais:

- JSON OAuth deve ter a chave `installed`, `client_id` e `client_secret`;
- o token fica em `token.json` na raiz, ignorado;
- a GUI memoriza o caminho escolhido em `data/youtube_client_secrets_path.txt`, também ignorado.

Metadados:

- título: primeira linha não vazia do TXT, até 100 caracteres;
- descrição: TXT sem URLs e sem `<`/`>`, até 5000 bytes UTF-8;
- categoria API: `22`;
- idioma padrão: `pt-BR`;
- quando há agendamento, o vídeo é enviado como `private` com `publishAt`.

Upload é resumível em chunks de 8 MB, sequencial, com retry para HTTP 500/502/503/504 e falhas de conexão.
Isso publica no YouTube, ao contrário do fluxo TikTok, que continua manual.

## 18. Mapa de arquivos

### Raiz e entrypoints

| Caminho | Responsabilidade |
|---|---|
| `main.py` | CLI de Cortes |
| `gui.py` | GUI Tkinter, abas, workers e ações de exportação/publicação |
| `web_main.py` | servidor FastAPI local |
| `web_worker.py` | worker RQ externo |
| `telegram_bot.py` | bot privado Telegram |
| `_venv_reexec.py` | reexecução dentro do venv |
| `workflow_historia.json` | workflow API do ComfyUI |
| `.env.example` | catálogo documentado de configuração |
| `requirements*.txt` | dependências |
| `baixarlinks.sh/.bat` | helpers legados para baixar/segmentar links.txt |
| `limpar_temp.sh` | remove temporários antigos, nunca resultados |
| `scripts/install_local_tts.sh` | instala Kokoro/PyTorch local |

### Núcleo

| Caminho | Responsabilidade |
|---|---|
| `app/core/config.py` | carrega `.env`, caminhos, encoder, GPU, TTS, limites e flags |
| `app/core/cache.py` | fingerprint, hash e JSON atômico |
| `app/core/cache_pipeline.py` | namespaces e contratos de cache |
| `app/core/limits.py` | semáforos e retry/backoff |
| `app/core/cancel.py` | cancelamento cooperativo global |
| `app/core/subprocess_utils.py` | subprocessos canceláveis |
| `app/core/logging_setup.py` | logging CLI/GUI e ponte para fila |
| `app/core/clip_output_naming.py` | sanitização de nomes |
| `app/core/caption_text.py` | remoção de links de descrições |
| `app/core/source_history.py` | histórico SQLite de URLs e claims |
| `app/core/linux_desktop_bootstrap.py` | defaults de threads/Mesa/ruído Linux |

### IA e legendas

| Caminho | Responsabilidade |
|---|---|
| `app/ai_integrations/groq_chat.py` | local LLM primeiro, Groq fallback, retry e rate limit |
| `app/ai_integrations/transcriber.py` | Groq Whisper, fatias e merge de timestamps |
| `app/ai_integrations/local_whisper.py` | faster-whisper em CUDA |
| `app/ai_integrations/translator.py` | Google Translate, cache LRU e batching |
| `app/ai_integrations/viral_analyzer.py` | prompt, parse, score, refinamento e seleção |
| `app/ai_integrations/tiktok_caption.py` | descrição/hashtags e crédito de fonte |
| `app/subtitle/formatter.py` | segundos → timestamp SRT |
| `app/subtitle/srt_generator.py` | SRT limpo, limitado e relativo ao clipe |
| `app/subtitle/ass_builder.py` | SRT → ASS normal/karaokê |

### Vídeo e áudio

| Caminho | Responsabilidade |
|---|---|
| `app/video_processing/audio_extractor.py` | MP3 mono 16 kHz para transcrição |
| `app/video_processing/video_splitter.py` | blocos completos de 20 minutos |
| `app/video_processing/video_cutter.py` | cutter auxiliar legado com speed-up |
| `app/video_processing/subtitle_burner.py` | corte + crop + ASS + overlays + encode |
| `app/video_processing/focal_crop.py` | smart crop MediaPipe/OpenCV |
| `app/video_processing/tts_dubber.py` | Edge/Kokoro dublado, alinhamento e trim de silêncio |
| `app/tts/tts_engine.py` | dispatch unificado TTS |
| `app/tts/tts_voices.py` | catálogo de vozes |
| `app/tts/local_tts.py` | Kokoro |
| `app/tts/gemini_tts.py` | Gemini REST TTS |
| `app/tts/tts_standalone.py` | MP3 avulso e preview |
| `app/gui/gui_export.py` | ffprobe, duração, ZIP e notificação |

### Pipelines de conteúdo

| Caminho | Responsabilidade |
|---|---|
| `app/pipelines/cortes/pipeline.py` | pipeline principal compartilhado |
| `app/pipelines/quiz/quiz_pipeline.py` | quiz completo |
| `app/pipelines/quiz/quiz_frames.py` | frames Pillow do quiz |
| `app/pipelines/batalha/batalha_pipeline.py` | spec, assets, TTS e orquestração Batalha |
| `app/pipelines/batalha/batalha_images.py` | buscas e tratamento de logos |
| `app/pipelines/batalha/batalha_frames.py` | Pymunk, simulações e render PIL |
| `app/pipelines/batalha/batalha_ffmpeg.py` | rawvideo, SFX e mux |
| `app/pipelines/historia/historia_pipeline.py` | cenas, TTS, ComfyUI e concat |
| `app/pipelines/historia/comfyui_client.py` | cliente HTTP mínimo ComfyUI |

### Web, analytics e publicação

| Caminho | Responsabilidade |
|---|---|
| `app/web/app.py` | factory FastAPI |
| `app/web/schemas.py` | modelos de resposta |
| `app/web/store.py` | playlist SQLite |
| `app/web/queue_backend.py` | RQ ou thread |
| `app/web/hub.py` | estado/SSE |
| `app/web/worker.py` | execução web |
| `app/web/tasks.py` | tarefas enfileiradas |
| `app/web/pipeline_form.py` | normalização de formulário |
| `app/web/routers/*.py` | endpoints jobs/playlist/progress/runs |
| `app/web/templates/index.html` | HTML web |
| `app/web/static/js/app.js` | comportamento do frontend |
| `app/analytics/performance.py` | CSV → recomendações |
| `app/analytics/content_profile.py` | JSON → perfil editorial |
| `app/analytics/retention_loop.py` | JSON → growth profile |
| `app/publishing/youtube_schedule.py` | datas/fuso |
| `app/publishing/youtube_uploader.py` | OAuth, metadata e upload |
| `app/gui/studio_theme.py` | tema visual da GUI |
| `app/gui/studio_motion.py` | tweens/animações Tkinter |

## 19. Configuração de ambiente

Todas as configurações novas devem ser adicionadas a `app/core/config.py` e documentadas em `.env.example`.
Evitar espalhar `os.getenv` por módulos novos.

### LLM e transcrição

| Variável | Padrão/uso |
|---|---|
| `GROQ_API_KEY` | chave para Groq; necessária quando o fallback Groq for usado |
| `GROQ_CHAT_MODEL` | `openai/gpt-oss-120b` |
| `GROQ_FAST_MODEL` | `openai/gpt-oss-20b` |
| `GROQ_REASONING_EFFORT` | `low`/`medium`/`high`, `low` recomendado para JSON |
| `LOCAL_LLM_BASE_URL` | servidor OpenAI-compatible opcional |
| `LOCAL_LLM_API_KEY` | chave opcional do LLM local |
| `LOCAL_LLM_MODEL` | modelo local; junto com base URL ativa local-first |
| `LOCAL_LLM_TIMEOUT_SEC` | 600 |
| `LOCAL_LLM_FAILURE_COOLDOWN_SEC` | 60 s antes de tentar local novamente após falha |
| `GROQ_HTTP_TIMEOUT_SEC` | timeout das chamadas Groq |
| `GROQ_MAX_IN_FLIGHT` | 2 |
| `GROQ_TRANSCRIBE_CHUNK_SEC` | 42 |
| `GROQ_TRANSCRIBE_SINGLE_MAX_SEC` | 42 |
| `GROQ_TRANSCRIBE_MAX_WORKERS` | 2, limitado pelo semáforo |
| `TRANSCRIBE_BACKEND` | `local` ou `groq`, default `local` com fallback |
| `LOCAL_WHISPER_MODEL` | `large-v3` |
| `LOCAL_WHISPER_COMPUTE` | `float16` |

### Saída, seleção e analytics

| Variável | Padrão/uso |
|---|---|
| `OUTPUT_DIR` | `resultados` |
| `TEMP_DIR` | `temp` |
| `CACHE_DIR` | cache do usuário/XDG |
| `SOURCE_HISTORY_DB` | `data/source_history.sqlite` |
| `GROWTH_PROFILE_PATH` | `data/growth_profile.json` |
| `CLIP_DURATION` | 50 s |
| `VIRAL_CLIPS_COUNT` | 5 |
| `VIRAL_CANDIDATE_COUNT` | 12, limitado a 10–20 |
| `VIRAL_SELECTION_PROFILE` | `tiktok_growth` |
| `TIKTOK_PERFORMANCE_REPORT_PATH` | vazio; ativa perfil de desempenho opcional |
| `CLIP_SPEED_UP_PERCENT` | 2% |

### Vídeo, legenda e crop

| Variável | Padrão/uso |
|---|---|
| `OUTPUT_VIDEO_WIDTH` / `HEIGHT` | 1080 / 1920 |
| `TIKTOK_SUBTITLE_FONT` | `Montserrat` |
| `TIKTOK_SUBTITLE_FONT_SIZE` | 40 |
| `TIKTOK_SUBTITLE_MARGIN_V` | 88 |
| `TIKTOK_SUBTITLE_MARGIN_LR` | 56 |
| `SUBTITLE_KARAOKE` | ligado |
| `SUBTITLE_KARAOKE_HIGHLIGHT` | `#FFE000` |
| `SMART_CROP_ENABLED` | ligado |
| `SMART_CROP_FRAME_SAMPLES` | 12 |
| `SMART_CROP_SPEAKER_FPS` | 4 |
| `SMART_CROP_MIN_CHANGE_INTERVAL_SEC` | 3 |
| `SMART_CROP_SPLIT_ENABLED` | ligado |
| `SMART_CROP_MEDIAPIPE_GPU` | auto; GPU normalmente com NVIDIA |
| `SMART_CROP_MEDIAPIPE_GPU_FORCE` | desligado |
| `VISUAL_GRADE` | ligado |
| `VISUAL_PROGRESS_BAR` | ligado |
| `VISUAL_PROGRESS_COLOR` | `yellow` |
| `VISUAL_WATERMARK_TEXT` | vazio |
| `OUTRO_CARD_DURATION_SEC` | 4 s, limitado entre 1,5 e 8 |

### Encode

| Variável | Padrão/uso |
|---|---|
| `USE_GPU_CLIP_ENCODE` | ligado |
| `CLIP_GPU_ENCODER` | auto por driver/encoders FFmpeg |
| `CLIP_ENCODE_PARALLEL_CPU` | 2 |
| `CLIP_ENCODE_PARALLEL_GPU` | 3 com driver NVIDIA, senão 2 |
| `VAAPI_RENDER_NODE` | auto, normalmente maior `renderD*` |
| `PIPELINE_MAX_WORKERS` | vazio; cálculo por CPU |
| `PIPELINE_CPU_FRACTION` | 0.65 |
| `PIPELINE_CPU_PER_CLIP_ESTIMATE` | 5 |

### TTS e dublagem

| Variável | Padrão/uso |
|---|---|
| `EDGE_TTS_VOICE` | `en-US-AriaNeural` |
| `EDGE_TTS_VOICE_PT` | `pt-BR-AntonioNeural` |
| `EDGE_TTS_REQUEST_TIMEOUT_SEC` | 180 |
| `EDGE_TTS_MAX_CONCURRENT` | 2 |
| `EDGE_TTS_RETRIES` | 6 |
| `DUB_MAX_TTS_SPEEDUP` | 4.0 |
| `DUB_TRIM_SILENCE` | ligado |
| `DUB_SILENCE_CUT_MIN_SEC` | 0.85 s |
| `DUB_SILENCE_DETECT_DB` | -40 dB |
| `LOCAL_TTS_DEVICE` | `auto` |
| `LOCAL_TTS_VOICE_PT` | `pf_dora` |
| `LOCAL_TTS_SPEED` | 1.0 |
| `LOCAL_TTS_PREFERRED` | ligado |
| `GEMINI_API_KEY` | ativa vozes Gemini |
| `GEMINI_TTS_MODEL` | `gemini-2.5-flash-preview-tts` |
| `GEMINI_TTS_VOICE_PT` | `Achernar` |
| `GEMINI_HTTP_TIMEOUT_SEC` | 180 |
| `TRANSLATE_BATCH` | ligado |
| `TRANSLATE_BATCH_MAX_CHARS` | 3800 |

### Download, web e publicação

| Variável | Padrão/uso |
|---|---|
| `DOWNLOAD_MAX_WORKERS` | 3, limitado a 10 |
| `YTDLP_PATH` / `YT_DLP_PATH` | executável explícito |
| `YTDLP_EXTRA_ARGS` | argumentos extras |
| `YTDLP_FORMAT` | formato yt-dlp explícito |
| `YTDLP_CONCURRENT_FRAGMENTS` | 4 |
| `YTDLP_COOKIES_FROM_BROWSER` | browser para cookies |
| `YTDLP_COOKIES_FILE` | arquivo Netscape |
| `YTDLP_JS_RUNTIMES` | runtime explícito, ex. `node:/caminho/node` |
| `YTDLP_REMOTE_COMPONENTS` | padrão `ejs:github` |
| `YTDLP_YOUTUBE_EXTRACTOR_ARGS` | estratégia manual |
| `YT_THEME_SEARCH_N` | 20 |
| `YT_THEME_MIN_DURATION_SEC` | 600 |
| `REDIS_URL` / `RQ_REDIS_URL` | ativa RQ/Redis |
| `RQ_QUEUE_NAME` | `cortes` |
| `WEB_HOST` | `127.0.0.1` |
| `WEB_PORT` | 8765 |
| `TELEGRAM_BOT_TOKEN` | token do BotFather |
| `TELEGRAM_ALLOWED_USER_ID` | ID numérico autorizado |
| `TIKTOK_UPLOAD_URL` | TikTok Studio upload |
| `YOUTUBE_CLIENT_SECRETS_FILE` | credencial OAuth |
| `YOUTUBE_SCHEDULE_TIMEZONE` | `America/Campo_Grande` |
| `CAPTION_SOURCE_ATTRIBUTION` | ligado |
| `CAPTION_SOURCE_LINE_PT` | `Review original: {channel}` |
| `CAPTION_SOURCE_LINE_EN` | `Original review: {channel}` |

## 20. Regras de manutenção para outra IA

1. Começar pela leitura deste arquivo, `.env.example`, `app/core/config.py` e
   `app/pipelines/cortes/pipeline.py`.
2. Não recriar um pipeline paralelo para CLI, GUI, web ou Telegram; todos os Cortes devem continuar chamando
   `run_pipeline`.
3. Manter `app/core/config.py` como ponto central de ambiente e documentar novas variáveis.
4. Preservar os contratos de segmentos, momentos, nomes de saída e estados da playlist.
5. Ao alterar prompts LLM, manter parse defensivo, limites e fallback local/temporal.
6. Ao alterar FFmpeg, sempre considerar caminhos com espaços, Windows/Linux, `drawtext`, áudio opcional,
   VA-API e fallback CPU.
7. Não remover o tratamento de segmentos que cruzam a borda do clipe.
8. Não trocar o corte+legenda por vários encodes sem uma razão de desempenho medida.
9. Não confiar em `GROQ_TRANSCRIBE_MAX_WORKERS` sem respeitar `GROQ_MAX_IN_FLIGHT`.
10. Não expor segredos no log, no manifest ou neste documento.
11. Testar nomes e caminhos com stems repetidos, acentos, caracteres inválidos e vídeos longos.
12. Para alterações visuais, lembrar que `assets/gui/*` são assets da GUI, enquanto o vídeo é renderizado por
    FFmpeg/Pillow e tem contratos próprios.
13. Ao adicionar um novo gerador, integrar a GUI via `job_type`, preservar logs/progresso/cancelamento e
    documentar se ele tem `.txt`, manifest, cache ou dependências externas.
14. Se um comportamento descrito aqui contradisser o código novo intencionalmente, atualizar primeiro este
    documento ou registrar explicitamente a mudança.

## 21. Limitações e riscos conhecidos no snapshot

- Não há autenticação na web local.
- Não há upload automático para TikTok/Instagram.
- Não há testes end-to-end confiáveis contra todas as APIs externas.
- Groq, Edge-TTS, Google Translate, yt-dlp, Wikimedia, DuckDuckGo, YouTube e ComfyUI podem falhar por rede,
  credencial, quota, formato ou mudança externa.
- O cache de segmentos do pipeline usa uma opção histórica com `impl="groq_whisper"`; a chave não diferencia
  explicitamente uma transcrição local de uma Groq. Ao alternar backend, pode ser necessário limpar o cache.
- O snapshot não contém os assets de áudio de quiz/batalha mencionados nos módulos; existem fallbacks, mas o
  design sonoro fica degradado.
- O helper de caminho do workflow de História precisa ser verificado porque o arquivo está na raiz e o cálculo
  atual parece apontar para `app/pipelines`.
- Encode de História e de alguns caminhos da Batalha usa diretamente o encoder configurado e não possui o
  mesmo fallback centralizado dos cortes; problemas de GPU podem aparecer nesses geradores primeiro.
- A GUI aplica toggles alterando constantes já importadas em memória. Isso é intencional para a sessão atual,
  mas não persiste as preferências em `.env`.
- A duração do loop de retenção é sugestão editorial; só tem efeito quando `CLIP_DURATION` não foi fixado.
- `faster-whisper` e Kokoro não são dependências base; a instalação local precisa ser feita separadamente.
- A seleção viral tem viés textual para conteúdo de música/guitarra; generalizar para outros nichos requer
  revisar prompt, entidades, hashtags e pesos.

## 22. Cobertura de testes

Os testes atuais cobrem principalmente:

- conversão e geração SRT/ASS;
- seleção viral, parse JSON, score, overlap, duplicatas e fallback;
- inclusão de segmentos parciais nas bordas;
- cache, fingerprint, namespaces e candidatos do manifest;
- retry e limites de concorrência;
- cancelamento cooperativo;
- escolha de encoder GPU e heurística de workers;
- split de vídeo longo e descarte do resto;
- tiktok caption, hashtags e crédito de fonte;
- smart crop split/static/dynamic em nível de filtergraph;
- geração de frames, filtros e fases do Quiz;
- especificação, imagens fallback, modos, Plinko, colisões e SFX da Batalha;
- perfil de conteúdo, CSV de desempenho e loop de retenção;
- catálogo TTS, Gemini parser e TTS standalone;
- store web e estados de playlist;
- parse de comandos Telegram;
- seleção, metadata, OAuth mockado e upload YouTube mockado.

Não cobrem de forma plena:

- pipeline completo com vídeo real;
- FFmpeg real em todas as plataformas;
- GPU NVIDIA/AMD/VA-API real;
- download YouTube real com cookies/runtime JS;
- chamadas Groq/Google Translate/Edge/Gemini reais;
- ComfyUI real e custom nodes;
- publicação real no YouTube;
- janela Tkinter em ambiente gráfico.

## 23. Primeiro diagnóstico quando algo falhar

1. Confirmar que o comando está sendo executado na raiz e que `.venv`/dependências existem.
2. Rodar `ffmpeg -version` e `ffprobe -version`; confirmar `drawtext` em `ffmpeg -filters`.
3. Verificar `.env` sem imprimir chaves em logs.
4. Para Groq, conferir `GROQ_API_KEY`, modelo vigente e limites `GROQ_MAX_IN_FLIGHT`.
5. Para transcrição local, conferir pacote `faster-whisper`, PyTorch CUDA e modelo baixado; caso contrário usar
   `TRANSCRIBE_BACKEND=groq` explicitamente.
6. Para smart crop, verificar download/cache do BlazeFace e testar com `SMART_CROP_ENABLED=0` para isolar o
   problema.
7. Para GPU, testar `USE_GPU_CLIP_ENCODE=0`; se resolver, configurar `CLIP_GPU_ENCODER`/`VAAPI_RENDER_NODE`
   ou revisar driver/encoder FFmpeg.
8. Para YouTube, verificar yt-dlp, runtime JS e cookies.
9. Para História, verificar ComfyUI em `127.0.0.1:8188`, workflow, checkpoint e Impact Pack; depois verificar
   o problema de caminho descrito acima.
10. Consultar `temp/` apenas durante diagnóstico; usar `limpar_temp.sh` ou o botão da GUI para apagar arquivos
    antigos. Nunca usar a limpeza de `temp/` durante um job ativo.
