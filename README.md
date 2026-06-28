# SaaS de Cortes Virais

Pipeline automatizado que recebe um vídeo longo, detecta os 5 melhores momentos virais e gera clipes de 50 segundos com legendas hardcoded no idioma escolhido.

## Dependências

### Python (instalação automática)
```bash
pip install -r requirements.txt
```

Opcional (download de vídeo por CLI, fora do pipeline): `pip install -r requirements-extra.txt`

| Pacote | Versão mínima | Uso |
|---|---|---|
| `groq` | 0.11.0 | Transcrição e análise de trechos via API Groq |
| `deep-translator` | 1.11.4 | Tradução dos segmentos de fala |
| `python-dotenv` | 1.0.0 | Carregamento de variáveis de ambiente |

### Sistema
- **FFmpeg 4.x+** — processamento de vídeo (corte, extração de áudio, queima de legendas)
  - Windows: `winget install ffmpeg` ou `choco install ffmpeg`
  - Linux/macOS: `sudo apt install ffmpeg` / `brew install ffmpeg`

## Configuração

1. Copie `.env.example` para `.env`:
```bash
cp .env.example .env
```

2. Preencha sua chave da API Groq em `.env`:
```
GROQ_API_KEY=gsk_...
```

## Como rodar

```bash
python main.py <caminho_do_video> [opções]
```

### Exemplos

```bash
# Gerar 5 clipes em português com configurações padrão
python main.py meu_video.mp4

# Gerar clipes em inglês
python main.py meu_video.mp4 --lang en

# Personalizar legendas
python main.py meu_video.mp4 --lang pt --color "#FFFFFF" --bg-color "#000000" --opacity 80 --position bottom
```

### Todas as opções

| Argumento | Padrão | Descrição |
|---|---|---|
| `video` | — | Caminho para o vídeo de entrada (obrigatório) |
| `--lang` | `pt` | Idioma de destino: `pt` ou `en` |
| `--position` | `bottom` | Posição das legendas: `bottom` ou `top` |
| `--font` | `Arial` | Nome da fonte das legendas |
| `--color` | `#FFFF00` | Cor do texto em hexadecimal |
| `--bg-color` | `#000000` | Cor de fundo em hexadecimal |
| `--opacity` | `75` | Opacidade do fundo (0–100) |

## Saída

Os vídeos finais são salvos em `resultados/` com o padrão:
```
resultados/<nome_do_video>_viral_1.mp4
resultados/<nome_do_video>_viral_2.mp4
...
resultados/<nome_do_video>_viral_5.mp4
```

## Estrutura do projeto

```
meu_saas_cortes/
├── main.py                          # Ponto de entrada (orquestrador mínimo)
├── requirements.txt
├── .env.example
├── app/
│   ├── config.py                    # Configurações centralizadas
│   ├── pipeline.py                  # Pipeline principal
│   ├── video_processing/
│   │   ├── audio_extractor.py       # Extração de áudio com FFmpeg
│   │   ├── video_cutter.py          # Corte de vídeo com FFmpeg
│   │   └── subtitle_burner.py       # Queima de legendas com FFmpeg
│   ├── ai_integrations/
│   │   ├── transcriber.py           # Transcrição via API Groq (Whisper)
│   │   ├── viral_analyzer.py        # Análise de momentos virais
│   │   └── translator.py            # Tradução via Google Translate
│   └── subtitle/
│       ├── formatter.py             # Formatação de timestamps SRT
│       └── srt_generator.py         # Geração de arquivos .srt
├── resultados/                      # Vídeos finais gerados
└── temp/                            # Arquivos temporários (auto-limpos)
```
