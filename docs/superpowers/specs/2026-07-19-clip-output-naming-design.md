# Design: Nomeação legível dos arquivos de saída (cortes)

**Data:** 2026-07-19  
**Status:** aprovado (conversa) — aguardando implementação  
**Escopo:** pipeline de Cortes Virais (`run_pipeline` / CLI / GUI)

## Problema

Os MP4 finais usam `{índice}_{stem_do_arquivo}.mp4` (ex.: `1_The Matrix Resurrections 2021 WEBRip 1080p.mp4` ou `1_ytdl_514bda5a….mp4`). Na pasta `resultados/`, fica difícil identificar o filme/vídeo na hora de postar.

## Objetivo

Nomes curtos e legíveis no dia a dia: **`{índice}_{slug}.mp4`**, com `.txt` de legenda no mesmo stem.

Exemplos:

| Entrada | Slug | Saída |
|---------|------|--------|
| `The Matrix Resurrections 2021.mkv` | `Matrix_Resurrections` | `1_Matrix_Resurrections.mp4` |
| `Inception 2010.mp4` | `Inception` | `1_Inception.mp4` |
| Apelido GUI/CLI `batman` | `batman` | `1_batman.mp4` |

## Decisão de produto

- **Fonte do slug:** heurística a partir do nome do arquivo (stem), **não** IA.
- **Override opcional:** apelido na CLI (`--slug`) e campo na GUI.
- **Formato:** índice primeiro → `1_Matrix.mp4` (não `Matrix_1.mp4`).
- **Sem hook** no nome do arquivo nesta entrega.
- **Telegram / quiz / batalha / história:** fora de escopo (Telegram só herda a heurística automática via `run_pipeline`, sem novo parâmetro de comando).

## API de nomeação

Módulo existente: `app/core/clip_output_naming.py`.

### Funções

1. **`sanitize_clip_output_stem(name, max_len=160)`** — mantém (já existe).
2. **`short_title_slug(stem, *, max_words=2, fallback_max_len=24) -> str`** — nova.
3. **`build_clip_output_stem(clip_index, video_stem, *, slug_override=None) -> str`** — nova; retorna stem completo sem extensão, ex. `1_Matrix`.

### Algoritmo `short_title_slug`

1. Se `slug_override` for passado e, após `sanitize_clip_output_stem`, não ficar vazio → usar esse resultado (Title_Case opcional não obrigatório no override; preservar o que o usuário digitou após sanitize).
2. Caso contrário, a partir de `stem`:
   - Trocar `.`, `-`, `_` e espaços por separadores e tokenizar.
   - Descartar tokens que casem com:
     - anos: `^(19|20)\d{2}$`
     - tags de mídia (case-insensitive): `1080p`, `720p`, `480p`, `2160p`, `4k`, `8k`, `webrip`, `bluray`, `brrip`, `dvdrip`, `hdtv`, `x264`, `x265`, `h264`, `h265`, `aac`, `hdr`, `remux`, `proper`, `extended`, `ytdl`
     - hashes / ids opacos: token só alfanumérico com comprimento ≥ 10 e poucas vogais, ou prefixo típico de download
   - Descartar stopwords PT/EN: `the`, `a`, `an`, `o`, `os`, `as`, `de`, `da`, `do`, `das`, `dos`, `um`, `uma`, `e`, `and`, `of`, `in`, `on`, `at`, `para`, `por`, `com`
   - Manter até `max_words` (padrão **2**) tokens significativos.
   - Unir com `_`; capitalizar a primeira letra de cada token (Title_Case simples).
3. Se nenhum token sobrar → `sanitize_clip_output_stem(stem, max_len=fallback_max_len)`.
4. Passar o resultado final por `sanitize_clip_output_stem` (limite de tamanho razoável, ex. 40–80 para o slug sozinho).

### Colisões multi-arquivo

Comportamento atual em `run_pipeline`: stems duplicados na fila recebem sufixo `__2`, `__3` no `video_name_override`. Continua igual — o sufixo faz parte do stem usado para gerar o slug (ou o override único + sufixo, conforme a ordem atual de construção do override).

Regra prática: se `output_slug` estiver definido, o override de colisão deve produzir slugs distintos, ex. `batman`, `batman__2` (aplicar o sufixo **depois** do slug base).

## Integração

### Pipeline

- `run_pipeline(..., output_slug: str | None = None)`
- `_run_single_pipeline(..., output_slug: str | None = None)`
- Em `_process_one_clip` (ou equivalente onde hoje há):

```python
out_stem = f"{clip_index}_{sanitize_clip_output_stem(video_name)}"
```

substituir por:

```python
out_stem = build_clip_output_stem(clip_index, video_name, slug_override=output_slug)
```

Nota: quando `output_slug` já foi fundido no `video_name` (colisão), passar `slug_override=None` e deixar a heurística ler o `video_name` já sufixado **ou** passar o slug efetivo já resolvido uma vez por vídeo — preferir **resolver o slug uma vez por vídeo** no início de `_run_single_pipeline` e reutilizar nos clipes (evita recalcular e deixa o manifest claro).

### CLI (`main.py`)

- `--slug NOME` opcional → `run_pipeline(..., output_slug=args.slug)`.

### GUI (`gui.py`)

- Campo opcional «Apelido do arquivo» na aba Cortes (placeholder `ex.: Matrix`).
- Vazio → heurística; preenchido → `output_slug`.

### Telegram

- Sem mudança de comando; herda heurística via pipeline.

## Documentação

Atualizar na mesma entrega:

- `projeto.md` — seção «Nomenclatura de saída (oficial)»
- `AI_CONTEXT.md` — padrão `{índice}_{slug}` + menção a `--slug` / apelido GUI + `short_title_slug` / `build_clip_output_stem`

## Testes

Arquivo: `tests/test_pipeline_output_stem.py` (expandir).

Casos mínimos:

| Input | Esperado (slug sozinho) |
|-------|-------------------------|
| `The Matrix Resurrections 2021` | `Matrix` ou `Matrix_Resurrections` (≤2 palavras; preferir primeiras significativas → `Matrix_Resurrections`) |
| `Movie.Name.2020.1080p.WEBRip.x264` | `Movie_Name` |
| `ytdl_514bda5a94f24646` | fallback sanitizado curto (não vazio) |
| override `batman` | `batman` (após sanitize) |
| `build_clip_output_stem(1, "The Matrix 2021")` | `1_Matrix` (ou `1_Matrix_…` conforme regra de 2 palavras) |
| stopwords-only stem | fallback não vazio |

Ajuste fino: com `max_words=2`, `The Matrix Resurrections 2021` → tokens `Matrix`, `Resurrections` → **`Matrix_Resurrections`**. Filme de uma palavra (`Inception 2010`) → **`Inception`**.

## Fora de escopo

- Renomear arquivos já existentes em `resultados/`
- Incluir hook/reason/timestamp no nome
- Resumo via Groq
- Alterar nomes de quiz / batalha / história
- Novo parâmetro no bot Telegram

## Critérios de aceite

1. Novo corte de `The Matrix Resurrections 2021.mp4` gera `1_Matrix_Resurrections.mp4` (e `.txt` irmão) sem `--slug`.
2. Com `--slug batman` (ou campo GUI), gera `1_batman.mp4`.
3. `pytest tests/test_pipeline_output_stem.py` passa.
4. `projeto.md` e `AI_CONTEXT.md` descrevem o padrão novo.
