import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from app.ai_integrations.groq_chat import groq_user_message_text
from app.core.caption_text import remove_links_from_caption
from app.core.config import GROQ_FAST_MODEL

if TYPE_CHECKING:
    from app.download.ytdlp_download import VideoSourceAttribution

_log = logging.getLogger("tiktok_caption")

_CAPTION_MAX_WORDS = 15
# Post: de 3 a 5 hashtags ligadas ao conteúdo. Tags genéricas de viralização são descartadas.
_LLM_CONTENT_HASHTAGS = 3
_MIN_HASHTAGS = 3
_MAX_HASHTAGS = 5
_FIXED_FYP_TAGS: tuple[str, ...] = ()  # símbolo legado; não reserva espaço no post

_GENERIC_HASHTAG_WORDS = frozenset(
    {
        "fyp",
        "fy",
        "foryou",
        "foryoupage",
        "viral",
        "viralizaai",
        "viralshorts",
        "trend",
        "trending",
        "trendingnow",
        "plotwist",
        "plottwist",
        "pov",
        "react",
        "reactbr",
        "desafio",
        "challenge",
        "shorts",
        "short",
        "clip",
        "cortes",
        "cortesvirais",
        "mustwatch",
        "watchtillend",
        "naotapega",
        "fypbrasil",
        "tiktok",
        "video",
        "vídeo",
    }
)

_STOPWORDS = frozenset(
    {
        "a",
        "o",
        "os",
        "as",
        "um",
        "uma",
        "uns",
        "umas",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "por",
        "para",
        "com",
        "sem",
        "que",
        "e",
        "ou",
        "se",
        "não",
        "nao",
        "mais",
        "muito",
        "como",
        "isso",
        "essa",
        "esse",
        "ele",
        "ela",
        "eles",
        "elas",
        "the",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        "my",
        "your",
        "his",
        "her",
        "their",
        "our",
        "not",
        "just",
        "so",
        "very",
        "what",
        "when",
        "where",
        "who",
        "why",
        "how",
        "all",
        "can",
        "will",
        "would",
        "could",
        "should",
        "have",
        "has",
        "had",
        "does",
        "did",
        "about",
        "from",
        "into",
        "than",
        "then",
        "there",
        "here",
        "also",
        "only",
        "even",
        "like",
        "get",
        "got",
        "one",
        "two",
        "three",
    }
)

_EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(text: str) -> str:
    text = _EMOJI_RE.sub("", text)
    return text.replace("\ufe0f", "").strip()


def _build_caption_prompt(
    clip_transcript: str,
    language: str,
    *,
    hook: str | None = None,
    category: str | None = None,
    topic: str | None = None,
) -> str:
    """TikTok *post description* (not burned-in subtitles)."""
    transcript = (clip_transcript or "").strip()
    if not transcript:
        transcript = "(no transcript text available for this clip)"

    hook_line = ""
    hook_text = (hook or "").strip()
    if hook_text:
        hook_line = f"\nHook on-screen (same clip): {hook_text}\n"
    category_line = f"\nCategoria do corte: {category}\n" if category else ""
    topic_line = f"Tema detectado: {topic}\n" if topic else ""

    lang = (language or "pt").strip().lower()
    banned_tags = ", ".join(
        sorted(
            {
                "plotwist",
                "pov",
                "react",
                "viral",
                "desafio",
                "fyp",
                "fy",
                "foryou",
                "trend",
                "shorts",
                "cortesvirais",
            }
        )
    )
    if lang == "pt":
        ctx_label = "--- TRANSCRIÇÃO DESTE CLIPE (use só isso como fonte) ---"
        rules = (
            "Regras de saída (obrigatório):\n"
            "- Idioma: português brasileiro (pt-BR).\n"
            f"- Linha principal (`caption`): no máximo {_CAPTION_MAX_WORDS} palavras. "
            "Resuma o que acontece ou é dito NESTE clipe — tema, fato, opinião ou momento-chave. "
            "Pode ser direto e chamativo, mas precisa bater com a transcrição. "
            "Proibido inventar cenas, nomes ou fatos que não aparecem no texto.\n"
            "- Proibido frases genéricas vazias: \"você não vai acreditar\", \"plot twist\", "
            "\"POV:\", \"react\", \"ninguém tá falando disso\", \"assista até o fim\" — "
            "a menos que isso esteja literalmente no clipe.\n"
            "- Zero emojis.\n"
            f"- `hashtags`: entre **{_MIN_HASHTAGS} e {_MAX_HASHTAGS}**, todas sobre o ASSUNTO do clipe "
            "(tema, nicho, pessoa/marca citada, esporte, filme, receita, etc.). "
            "Cada hashtag deve ser compreensível só lendo a transcrição.\n"
            f"- Proibido hashtags genéricas de viralização ou FYP: {banned_tags}.\n"
            "- Não use #fyp, #fy, #foryou nem #foryoupage.\n"
            "- Hashtags começam com #, sem espaços, sem acentos se possível (ex.: #futebol, #cinema).\n\n"
            'Responda APENAS com JSON válido (sem markdown, sem ```):\n'
            '{"caption":"<frase sobre o conteúdo real>","hashtags":["#TemaDoClip","#Nicho","#Assunto"]}\n'
        )
    else:
        ctx_label = "--- CLIP TRANSCRIPT (use only this as source) ---"
        rules = (
            "Output rules (strict):\n"
            "- Language: English.\n"
            f"- Main line (`caption`): at most {_CAPTION_MAX_WORDS} words. "
            "Summarize what happens or is said in THIS clip — topic, fact, opinion or key moment. "
            "It can be punchy, but must match the transcript. "
            "Do not invent scenes, names or facts not in the text.\n"
            "- Do not use empty generic hooks: \"you won't believe\", \"plot twist\", \"POV:\", "
            "\"react\", \"nobody is talking about this\", \"watch till the end\" — "
            "unless that literally appears in the clip.\n"
            "- Zero emojis.\n"
            f"- `hashtags`: between **{_MIN_HASHTAGS} and {_MAX_HASHTAGS}**, all about the clip SUBJECT "
            "(topic, niche, person/brand mentioned, sport, movie, recipe, etc.). "
            "Each hashtag must make sense from the transcript alone.\n"
            f"- Forbidden generic viral/FYP tags: {banned_tags}.\n"
            "- Do not use #fyp, #fy, #foryou, or #foryoupage.\n"
            "- Hashtags start with #, no spaces.\n\n"
            "Respond with ONLY valid JSON (no markdown, no code fences):\n"
            '{"caption":"<line about actual content>","hashtags":["#ClipTopic","#Niche","#Subject"]}\n'
        )

    return (
        "You write TikTok **post captions** — the description text when uploading a video. "
        "This is NOT on-video subtitles, NOT SRT, and NOT burned-in captions.\n\n"
        "Your job: describe THIS specific clip accurately so a viewer knows what it is about.\n\n"
        f"{ctx_label}\n"
        f"{transcript}\n"
        f"{hook_line}"
        f"{category_line}"
        f"{topic_line}"
        "--- END ---\n\n"
        f"{rules}"
    )


def _extract_json_object(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model response")
    return text[start : end + 1]


def _enforce_word_limit(text: str, max_words: int = _CAPTION_MAX_WORDS) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _normalize_hashtags(tags: list, *, max_tags: int | None = None) -> list[str]:
    out: list[str] = []
    for raw in tags:
        if not isinstance(raw, str):
            continue
        t = raw.strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t.lstrip("#")
        t = _strip_emojis(t)
        t = "#" + re.sub(r"[^a-zA-ZÀ-ÿ0-9]", "", t.lstrip("#"))
        tag_word = re.sub(r"[^a-zA-Z0-9]", "", t.lstrip("#")).lower()
        if tag_word in _GENERIC_HASHTAG_WORDS:
            continue
        if t and t != "#":
            out.append(t)
        if max_tags is not None and len(out) >= max_tags:
            break
    return out


def _content_hashtags_from_transcript(
    transcript: str,
    *,
    count: int = _LLM_CONTENT_HASHTAGS,
) -> list[str]:
    """Hashtags de fallback derivadas de palavras-chave da transcrição."""
    canonical_terms = (
        ("guitarra", "#Guitarra"),
        ("violão", "#Violao"),
        ("violao", "#Violao"),
        ("blues", "#Blues"),
        ("rock", "#Rock"),
        ("pentatônica", "#Pentatonica"),
        ("pentatonica", "#Pentatonica"),
        ("improvisação", "#Improvisacao"),
        ("improvisacao", "#Improvisacao"),
        ("acorde", "#Acordes"),
        ("acordes", "#Acordes"),
        ("escala", "#Escalas"),
        ("escalas", "#Escalas"),
        ("timbre", "#Timbre"),
        ("riff", "#Riff"),
        ("solo", "#Solo"),
        ("composição", "#Composicao"),
        ("composicao", "#Composicao"),
        ("teoria musical", "#TeoriaMusical"),
    )
    lowered = (transcript or "").casefold()
    out: list[str] = []
    seen: set[str] = set()
    for term, tag in canonical_terms:
        if term in lowered and tag.casefold() not in seen:
            out.append(tag)
            seen.add(tag.casefold())
            if len(out) >= count:
                return out
    words = re.findall(r"[a-zA-ZÀ-ÿ0-9]{4,}", (transcript or "").lower())
    for word in words:
        normalized = re.sub(r"[^a-z0-9]", "", word)
        if len(normalized) < 4:
            continue
        if normalized in _STOPWORDS or normalized in _GENERIC_HASHTAG_WORDS:
            continue
        if normalized in seen:
            continue
        out.append(f"#{normalized}")
        seen.add(normalized)
        if len(out) >= count:
            break
    return out


def _finalize_tiktok_hashtags(
    normalized: list[str],
    language: str,
    *,
    transcript: str = "",
) -> list[str]:
    """Mantém de 3 a 5 hashtags específicas, sem reservar espaço para FYP."""
    del language
    content: list[str] = []
    seen: set[str] = set()
    for h in normalized:
        hl = h.lower()
        tag_word = re.sub(r"[^a-zA-Z0-9]", "", h.lstrip("#")).lower()
        if tag_word in _GENERIC_HASHTAG_WORDS or hl in seen:
            continue
        content.append(h)
        seen.add(hl)
        if len(content) >= _MAX_HASHTAGS:
            break
    for b in _content_hashtags_from_transcript(transcript, count=_MAX_HASHTAGS):
        if len(content) >= _MAX_HASHTAGS:
            break
        bl = b.lower()
        if bl in seen:
            continue
        content.append(b)
        seen.add(bl)
    return content[:_MAX_HASHTAGS]


def _fallback_caption(clip_transcript: str, language: str) -> str:
    transcript = (clip_transcript or "").strip()
    base = _enforce_word_limit(_strip_emojis(transcript))
    lang = (language or "pt").strip().lower()
    if not base:
        base = "Assista a esse corte" if lang == "pt" else "Watch this clip"
    tags = _finalize_tiktok_hashtags(
        _content_hashtags_from_transcript(transcript),
        lang,
        transcript=transcript,
    )
    return base + ("\n" + " ".join(tags) if tags else "")


def generate_tiktok_post_caption(
    clip_transcript: str,
    language: str = "pt",
    *,
    hook: str | None = None,
    category: str | None = None,
    topic: str | None = None,
) -> str:
    """
    Texto da caixa de descrição do post (TikTok) no mesmo idioma que `language` (pt ou en).
    Não gera legendas queimadas no vídeo.
    """
    lang = (language or "pt").strip().lower()
    transcript = (clip_transcript or "").strip()
    prompt = _build_caption_prompt(
        transcript,
        lang,
        hook=hook,
        category=category,
        topic=topic,
    )
    try:
        llm_text = groq_user_message_text(
            prompt,
            temperature=0.35,
            max_tokens=512,
            none_as_empty=True,
            retry_label="legenda TikTok",
            bad_request_runtime=lambda e: RuntimeError(f"Prompt inválido para legenda TikTok: {e}"),
            rate_limit_message=(
                "Groq rate limit excedido ao gerar legenda de postagem. Tente novamente."
            ),
            model=GROQ_FAST_MODEL,
        )
        content = llm_text or ""
        obj_txt = _extract_json_object(content)
        data = json.loads(obj_txt)
    except (ValueError, json.JSONDecodeError, RuntimeError) as e:
        _log.warning("Geração da legenda TikTok falhou (%s). Usando fallback.", e)
        return _fallback_caption(transcript, lang)

    caption = data.get("caption", "")
    if not isinstance(caption, str):
        caption = str(caption)
    caption = _strip_emojis(caption).strip()
    caption = _enforce_word_limit(caption)

    hashtags = data.get("hashtags", [])
    if not isinstance(hashtags, list):
        hashtags = []
    hashtags = _finalize_tiktok_hashtags(
        _normalize_hashtags(hashtags),
        lang,
        transcript=transcript,
    )

    if not caption:
        return _fallback_caption(transcript, lang)

    lines = [caption, " ".join(hashtags)]
    return "\n".join(lines).strip()


def _source_attribution_enabled() -> bool:
    return os.getenv("CAPTION_SOURCE_ATTRIBUTION", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _source_attribution_line(channel: str, language: str) -> str:
    lang = (language or "pt").strip().lower()
    if lang == "en":
        template = os.getenv(
            "CAPTION_SOURCE_LINE_EN",
            "Original review: {channel}",
        )
    else:
        template = os.getenv(
            "CAPTION_SOURCE_LINE_PT",
            "Review original: {channel}",
        )
    return template.format(channel=channel.strip())


def append_source_attribution_to_caption(
    caption_text: str,
    attribution: "VideoSourceAttribution | None",
    *,
    language: str = "pt",
) -> str:
    """Acrescenta crédito ao canal (ex.: review do YouTube) após legenda e hashtags."""
    if not _source_attribution_enabled() or attribution is None:
        return caption_text
    channel = (attribution.channel or "").strip()
    if not channel:
        return caption_text

    base = (caption_text or "").strip()
    lines = [base] if base else []
    lines.append(_source_attribution_line(channel, language))
    return "\n".join(lines).strip() + "\n"


def save_tiktok_caption_file(video_path: str, caption_text: str) -> str:
    """Salva legenda ao lado do .mp4 com o mesmo nome base e extensão .txt"""
    p = Path(video_path)
    out = p.with_suffix(".txt")
    clean_caption = remove_links_from_caption(caption_text)
    out.write_text(clean_caption + "\n", encoding="utf-8")
    return str(out)
