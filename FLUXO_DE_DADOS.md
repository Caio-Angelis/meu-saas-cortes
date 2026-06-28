# Fluxo de Dados e Guia de Uso — SaaS de Cortes Virais

Este documento detalha o fluxo de processamento de vídeos no sistema de geração automática de cortes virais, suas entradas, saídas e como iniciar o uso.

## Visão Geral

O sistema recebe um **vídeo longo** como entrada e, através de um pipeline automatizado, produz **5 vídeos curtos (aproximadamente 50 segundos cada)**. Estes vídeos curtos contêm legendas traduzidas e "queimadas" (hardcoded), prontas para publicação em redes sociais.

## Pré-requisitos

Antes de usar o sistema, certifique-se de ter instalado:

- **FFmpeg**: Ferramenta essencial para processamento de áudio e vídeo.
    - **Windows 10/11**: `winget install ffmpeg`
    - **Ubuntu/Debian**: `apt install ffmpeg`
    - **macOS**: `brew install ffmpeg`
    O caminho do FFmpeg é detectado automaticamente pelo sistema.

- **Python 3.10+**: A linguagem de programação principal do projeto.

## Configuração de Ambiente

Crie um arquivo `.env` na raiz do projeto com as seguintes variáveis:

```env
GROQ_API_KEY=gsk_...          # Obrigatório: chave da API Groq para transcrição e análise de trechos.
OUTPUT_DIR=resultados          # Opcional: pasta onde os vídeos finais serão salvos (padrão: resultados).
TEMP_DIR=temp                  # Opcional: pasta para arquivos temporários (padrão: temp).
CLIP_DURATION=50               # Opcional: duração de cada clipe viral em segundos (padrão: 50).
VIRAL_CLIPS_COUNT=5            # Opcional: número de clipes virais a serem gerados (padrão: 5).
```

**Importante**: A `GROQ_API_KEY` é obrigatória para transcrição e para a etapa que identifica os momentos virais na API Groq.

## Fluxo de Dados Detalhado

O pipeline de processamento segue estas etapas:

```
[Vídeo de entrada]
        │
        ▼
[1] Extração de Áudio (audio_extractor.py)
    O FFmpeg extrai a trilha de áudio do vídeo de entrada e a salva temporariamente em `temp/<nome>.mp3`.
        │
        ▼
[2] Transcrição (transcriber.py)
    A API Groq (modelo Whisper large-v3) transcreve o áudio, gerando uma lista de segmentos de texto com seus timestamps (`start`, `end`, `text`). O arquivo de áudio temporário é removido.
        │
        ▼
[3] Análise de Viralidade (viral_analyzer.py)
    A transcrição completa é enviada à API Groq (chat completions). O serviço retorna uma estrutura com 5 "momentos virais", cada um com `start`, `end` e um `reason`, com duração alinhada à configuração (ex.: 50 segundos).
        │
        ▼  (Para cada um dos 5 momentos virais identificados)
[4a] Tradução de Legendas (translator.py)
     O Google Translator (via `deep-translator`) traduz os segmentos de texto de cada momento viral para o idioma desejado.
     
[4b] Corte de Vídeo (video_cutter.py)
     O FFmpeg corta o vídeo original, isolando o segmento exato do momento viral e salvando-o temporariamente em `temp/<nome>_clip_N.mp4`.

[4c] Geração de SRT (srt_generator.py)
     É gerado um arquivo `.srt` com as legendas traduzidas, com os timestamps ajustados para o início do clipe cortado (offset). Este arquivo é salvo temporariamente em `temp/<nome>_clip_N.srt`.

[4d] Queima de Legendas (subtitle_burner.py)
     O FFmpeg "queima" as legendas geradas no clipe de vídeo cortado, incorporando-as permanentemente. Este é o vídeo final.
        │  (Todos os arquivos temporários específicos deste clipe são removidos)
        ▼
[Saída Final: 5 vídeos virais em `resultados/`]
```

## Como Usar

Para executar o pipeline, use o seguinte comando no terminal, substituindo `<caminho_do_video>` pelo caminho completo do seu vídeo de entrada:

```bash
python main.py <caminho_do_video>
```

**Exemplo:**

```bash
python main.py videos/meu_video_longo.mp4
```

### Opções Adicionais:

Você pode customizar a geração dos clipes usando as seguintes opções:

- `--lang [pt|en]`: Idioma de destino das legendas (padrão: `pt`)
- `--position [bottom|top]`: Posição das legendas no vídeo (padrão: `bottom`)
- `--font <nome_da_fonte>`: Fonte das legendas (padrão: `Arial`)
- `--color <hex_code>`: Cor do texto das legendas em formato hexadecimal (padrão: `#FFFF00` - amarelo)
- `--bg-color <hex_code>`: Cor de fundo das legendas em formato hexadecimal (padrão: `#000000` - preto)
- `--opacity <0-100>`: Opacidade do fundo das legendas (padrão: `75`)

**Exemplo com opções:**

```bash
python main.py videos/meu_video.mp4 --lang en --position top --color #FF00FF --opacity 50
```

## Entradas e Saídas

- **Entrada**: Um único arquivo de vídeo longo, passado como argumento para o `main.py`.
- **Saída**: 5 arquivos de vídeo MP4 curtos (aproximadamente 50 segundos cada) na pasta `resultados/` (ou na pasta especificada por `OUTPUT_DIR`), cada um com legendas hardcoded e traduzidas.

## Limpeza de Arquivos Temporários

Todos os arquivos intermediários (áudios, clipes cortados, arquivos SRT) são criados na pasta `TEMP_DIR` e removidos automaticamente após serem utilizados. No entanto, em caso de falha no pipeline, esses arquivos podem não ser removidos e precisarão de limpeza manual.
