# Cortes Lab

Studio local-first para transformar vídeos longos e ideias em conteúdo vertical pronto para TikTok,
Reels, Shorts e YouTube.

O projeto combina IA, FFmpeg, TTS e automações locais em uma única aplicação Python. Apesar do nome
conceitual “SaaS de Cortes Virais”, este repositório não é um SaaS hospedado: não possui login,
multi-tenancy, cobrança ou backend remoto próprio.

## O que ele faz

| Fluxo | Entrada | Resultado |
|---|---|---|
| Cortes Virais | vídeo local, vários vídeos ou URL | clipes 9:16 com legendas queimadas, descrição e manifest |
| Máquina de Quizzes | tema, dificuldade, timer e voz | quiz vertical com perguntas, timer e revelação |
| Batalha 1v1 | tema e modo de jogo | duelo vertical com física 2D, logos, narração e efeitos |
| História | texto e voz | história narrada com cenas geradas pelo ComfyUI |
| Text-to-Speech | texto e voz | MP3 com Kokoro, Gemini ou Edge-TTS |
| Analytics | CSV/JSON de desempenho | recomendações editoriais e perfil de crescimento |
| YouTube | 5 pares MP4/TXT | uploads privados agendados pela API oficial |

Interfaces disponíveis:

- GUI desktop em Tkinter — experiência principal e acesso a todos os fluxos;
- CLI — processamento rápido de Cortes Virais;
- web local em FastAPI — Cortes Virais com playlist persistida;
- bot privado do Telegram — execução remota no PC local.

## Destaques

- seleção de momentos virais com score local, diversidade e fallback temporal;
- transcrição local com faster-whisper quando CUDA está disponível, com fallback para Groq Whisper;
- tradução para português ou inglês;
- legendas ASS normais ou karaokê, adaptadas à safe area do TikTok;
- smart crop por rosto/falante com MediaPipe e modo split para duas pessoas;
- hook, CTA, grade visual, barra de progresso e watermark opcionais;
- dublagem alinhada aos timestamps com Edge-TTS ou Kokoro local;
- cache persistente para transcrição, análise, tradução e crop;
- processamento paralelo com controle de CPU/GPU;
- suporte a vídeos acima de 20 minutos em blocos completos de 20 minutos;
- descrição para TikTok com hashtags relacionadas ao conteúdo e crédito opcional da fonte.

## Requisitos

### Obrigatórios

- Python moderno — o projeto é desenvolvido com alvo Python 3.12;
- FFmpeg e ffprobe no PATH;
- dependências de requirements.txt;
- GROQ_API_KEY para os fluxos que precisarem usar Groq, salvo configuração totalmente local.

### Opcionais

- GPU NVIDIA/AMD/Intel e FFmpeg com encoder compatível;
- faster-whisper para transcrição local;
- Kokoro para TTS local;
- Node, Deno ou Bun e cookies para downloads mais confiáveis do YouTube;
- Redis para usar RQ na fila web;
- ComfyUI com Impact Pack/Impact Subpack para História;
- credencial OAuth de aplicativo para computador para publicar no YouTube.

## Instalação rápida

```bash
git clone https://github.com/Caio-Angelis/meu-saas-cortes.git
cd meu-saas-cortes

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edite .env e configure pelo menos:

```dotenv
GROQ_API_KEY=sua_chave_groq
```

Os entrypoints detectam .venv automaticamente. Usar .venv/bin/python explicitamente evita ambiguidades
entre instalações do sistema.

## Como executar

### GUI — recomendada

```bash
.venv/bin/python gui.py
```

A GUI Cortes Lab — Creative Automation Studio possui workspaces para Cortes Virais, Quiz, Batalha,
História, TTS, publicação no YouTube e análise de desempenho. Jobs longos rodam em background, com log,
progresso, cancelamento cooperativo, exportação ZIP e ações para copiar legendas.

### CLI — Cortes Virais

```bash
# 5 clipes em português
.venv/bin/python main.py meu_video.mp4

# Inglês e legenda no topo
.venv/bin/python main.py meu_video.mp4 --lang en --position top

# Vários vídeos na mesma execução
.venv/bin/python main.py video_1.mp4 video_2.mkv

# Dublagem opcional
.venv/bin/python main.py meu_video.mp4 --dub-en
.venv/bin/python main.py meu_video.mp4 --dub-pt --tts-voice pt-BR-FranciscaNeural
```

| Opção | Padrão | Valores |
|---|---|---|
| --lang | pt | pt, en |
| --position | bottom | bottom, top |
| --font | Arial | nome da fonte |
| --color | #FFFF00 | cor do texto |
| --bg-color | #000000 | fundo da legenda |
| --opacity | 75 | 0–100 |
| --dub-en / --dub-pt | desligado | apenas uma por vez |
| --tts-voice | vazio | voz selecionada |

### Web local

```bash
.venv/bin/python web_main.py
```

Abra http://127.0.0.1:8765/. A web permite enviar arquivos/URLs, adicionar itens à playlist, processar
a fila e acompanhar o progresso por SSE/polling.

Por padrão, os jobs rodam em uma thread no servidor. Para usar Redis + RQ:

```bash
export REDIS_URL=redis://127.0.0.1:6379/0
.venv/bin/python web_worker.py
```

Em outra janela, inicie web_main.py.

### Telegram

Configure no .env:

```dotenv
TELEGRAM_BOT_TOKEN=token_do_botfather
TELEGRAM_ALLOWED_USER_ID=seu_id_numerico
```

Depois execute:

```bash
.venv/bin/python telegram_bot.py
```

Comandos:

```text
/cortes <URLs ou caminhos, um por linha>
/tema curiosidades sobre o espaço
/quiz Geografia 5 5
/batalha plinko Batman vs Superman
/historia Era uma vez...
/tts Texto para locução
```

O bot aceita apenas o ID configurado e mantém um job por vez. Vídeos acima do limite do Telegram não são
enviados, mas o bot informa o caminho local.

## Fluxo dos Cortes Virais

```text
entrada local/URL
      ↓
blocos completos de 20 min
      ↓
fingerprint + cache
      ↓
transcrição local ou Groq Whisper
      ↓
candidatos virais via LLM
      ↓
ranking local + deduplicação + diversidade
      ↓
tradução + SRT/ASS + smart crop
      ↓
FFmpeg: corte + speed-up + overlays + encode
      ↓
dublagem opcional + descrição TikTok
      ↓
MP4 + TXT + manifest
```

O padrão é gerar 5 clipes de aproximadamente 50 segundos por bloco de 20 minutos, em 1080×1920.
O speed-up padrão é de 2%. Para fontes acima de 20 minutos, apenas blocos completos são processados;
o restante incompleto é descartado deliberadamente.

## Saídas

```text
resultados/1_nome_do_video.mp4
resultados/1_nome_do_video.txt
resultados/2_nome_do_video.mp4
```

O TXT contém a descrição para o post TikTok. O manifest registra fingerprint, opções, cache, seleção
dos momentos e arquivos gerados:

```text
resultados/nome_do_video__run_manifest_YYYYMMDD_HHMMSS.json
```

Outros fluxos usam resultados/quiz_<tema>_<timestamp>.mp4, resultados/batalha_<tema>_<timestamp>.mp4,
resultados/historias/historia_final_<timestamp>.mp4 e resultados/tts/<timestamp>_<texto>.mp3.

temp/, resultados/, data/, caches e credenciais são locais e ignorados pelo Git.

## Configuração

Todas as variáveis estão documentadas em [.env.example](.env.example). As mais importantes são:

| Variável | Função | Padrão |
|---|---|---|
| OUTPUT_DIR | saída dos arquivos | resultados |
| TEMP_DIR | temporários | temp |
| CLIP_DURATION | duração-alvo dos cortes | 50 |
| VIRAL_CLIPS_COUNT | quantidade de cortes | 5 |
| VIRAL_SELECTION_PROFILE | perfil do ranking | tiktok_growth |
| CLIP_SPEED_UP_PERCENT | aceleração | 2 |
| TRANSCRIBE_BACKEND | transcrição | local com fallback Groq |
| SMART_CROP_ENABLED | crop por rosto/falante | ligado |
| SUBTITLE_KARAOKE | legenda palavra a palavra | ligado |
| USE_GPU_CLIP_ENCODE | encoder GPU dos cortes | ligado |
| LOCAL_TTS_PREFERRED | prioriza Kokoro | ligado |
| TIKTOK_PERFORMANCE_REPORT_PATH | perfil histórico opcional | vazio |

### TTS local

```bash
bash scripts/install_local_tts.sh
```

Vozes Kokoro disponíveis: pf_dora, pf_sara, pm_alex e pm_santa.

### História com ComfyUI

O ComfyUI deve estar em http://127.0.0.1:8188, com:

- workflow_historia.json configurado;
- checkpoint v1-5-pruned-emaonly.ckpt;
- Impact Pack e Impact Subpack;
- modelo facial bbox/face_yolov8m.pt.

## Publicação no YouTube

A aba de publicação aceita exatamente 5 MP4 e 5 TXT correspondentes. Ela ordena os arquivos
naturalmente, usa a primeira linha do TXT como título, limpa a descrição, autentica via OAuth e agenda
uma publicação por dia, começando amanhã. Os vídeos ficam privados até o horário agendado.

Configure YOUTUBE_CLIENT_SECRETS_FILE ou selecione o JSON pela GUI. O token OAuth é salvo localmente em
token.json e nunca deve ser commitado.

## Analytics

A aba Análise de desempenho lê CSVs em português ou inglês, detecta colunas de tema, título, views,
engajamento, retenção e seguidores e retorna três próximos temas.

O Loop de retenção lê JSON exportado do TikTok, compara buckets de duração, horários, recursos da legenda
e ganhos de seguidores, e salva data/growth_profile.json. O pipeline usa a duração recomendada apenas
quando CLIP_DURATION não foi fixado explicitamente.

## Estrutura do projeto

```text
.
├── main.py                         # CLI de Cortes Virais
├── gui.py                          # GUI desktop e roteamento de jobs
├── web_main.py                     # servidor FastAPI
├── web_worker.py                   # worker RQ opcional
├── telegram_bot.py                 # bot privado Telegram
├── app/
│   ├── ai_integrations/            # Groq, Whisper, tradução e captions
│   ├── analytics/                  # CSV, perfil e feedback de retenção
│   ├── core/                       # configuração, cache, limites e cancelamento
│   ├── download/                   # yt-dlp, busca por tema e histórico
│   ├── pipelines/                  # cortes, quiz, batalha e história
│   ├── publishing/                 # agenda/upload YouTube
│   ├── subtitle/                   # SRT e ASS
│   ├── tts/                        # Kokoro, Gemini, Edge e TTS avulso
│   ├── video_processing/           # FFmpeg, crop, dublagem e split
│   └── web/                        # API, playlist, worker e frontend
├── assets/                         # fontes e assets versionáveis da aplicação
├── tests/                          # testes unitários
├── .env.example                    # configuração documentada
├── AI_context.md                   # contexto técnico para outras IAs
└── workflow_historia.json          # workflow do ComfyUI
```

## Desenvolvimento

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

Os testes cobrem parsing, ranking, cache, nomenclatura, filtros FFmpeg, estados da playlist, TTS,
analytics, simulações e integrações mockadas. Um teste real ainda exige vídeo, FFmpeg, rede, credenciais,
GPU e/ou ComfyUI, conforme o fluxo.

## Limitações importantes

- a aplicação é local e não oferece autenticação web;
- TikTok e Instagram não têm upload automático;
- serviços externos podem exigir quota, cookies, runtime JS ou credenciais;
- faster-whisper e Kokoro são opcionais e não vêm nas dependências base;
- os assets de áudio do Quiz/Batalha podem não estar presentes; há fallbacks, mas o resultado sonoro fica
  simplificado;
- o usuário é responsável pelos direitos do material baixado e publicado.

Para uma descrição operacional mais completa, consulte [AI_context.md](AI_context.md).

## Licença

Este repositório ainda não contém um arquivo LICENSE. Defina a licença antes de redistribuir o projeto.
