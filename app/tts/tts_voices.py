"""Catálogo de vozes TTS (local Kokoro + Gemini + Edge-TTS) para GUI e `tts_engine`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import (
    EDGE_TTS_VOICE,
    EDGE_TTS_VOICE_PT,
    GEMINI_TTS_VOICE_PT,
    LOCAL_TTS_PREFERRED,
    LOCAL_TTS_VOICE_PT,
)
from app.tts.gemini_tts import gemini_tts_available
from app.tts.local_tts import LOCAL_TTS_VOICES, local_tts_available

TtsProvider = Literal["local", "edge", "gemini"]


@dataclass(frozen=True)
class TtsVoiceOption:
    voice_id: str
    label: str
    provider: TtsProvider
    engine_voice: str
    premium: bool = False


def _local_options() -> list[TtsVoiceOption]:
    if not local_tts_available():
        return []
    return [
        TtsVoiceOption(
            voice_id=f"local:{voice_id}",
            label=label,
            provider="local",
            engine_voice=voice_id,
            premium=premium,
        )
        for voice_id, label, premium in LOCAL_TTS_VOICES
    ]


def _gemini_options() -> list[TtsVoiceOption]:
    if not gemini_tts_available():
        return []
    voices = [
        ("Achernar", "Achernar — feminina suave (Gemini cloud)", True),
        ("Leda", "Leda — feminina jovem e leve (Gemini cloud)", True),
        ("Vindemiatrix", "Vindemiatrix — feminina gentil (Gemini cloud)", False),
        ("Despina", "Despina — feminina suave (Gemini cloud)", False),
        ("Aoede", "Aoede — feminina descontraída (Gemini cloud)", False),
        ("Gacrux", "Gacrux — feminina madura (Gemini cloud)", False),
    ]
    return [
        TtsVoiceOption(
            voice_id=f"gemini:{name}",
            label=label,
            provider="gemini",
            engine_voice=name,
            premium=premium,
        )
        for name, label, premium in voices
    ]


def _edge_options() -> list[TtsVoiceOption]:
    items = [
        ("pt-BR-ThalitaMultilingualNeural", "Thalita multilíngue — feminina pt-BR (Edge)"),
        ("pt-BR-FranciscaNeural", "Francisca — feminina pt-BR (Edge)"),
        (EDGE_TTS_VOICE_PT, f"Antonio / padrão pt ({EDGE_TTS_VOICE_PT})"),
        ("pt-BR-DonatoNeural", "Donato — masculina pt-BR (Edge)"),
        (EDGE_TTS_VOICE, f"Inglês padrão ({EDGE_TTS_VOICE})"),
        ("en-US-GuyNeural", "Guy — masculino en-US (Edge)"),
        ("en-GB-SoniaNeural", "Sonia — feminina en-GB (Edge)"),
    ]
    seen: set[str] = set()
    out: list[TtsVoiceOption] = []
    for engine, label in items:
        if engine in seen:
            continue
        seen.add(engine)
        out.append(
            TtsVoiceOption(
                voice_id=f"edge:{engine}",
                label=label,
                provider="edge",
                engine_voice=engine,
            )
        )
    return out


def all_tts_voice_options() -> list[TtsVoiceOption]:
    """Local (Kokoro) primeiro; depois Gemini; depois Edge."""
    return _local_options() + _gemini_options() + _edge_options()


def gui_voice_labels() -> tuple[str, ...]:
    return tuple(o.label for o in all_tts_voice_options())


def default_voice_id() -> str:
    if local_tts_available() and LOCAL_TTS_PREFERRED:
        name = (LOCAL_TTS_VOICE_PT or "pf_dora").strip()
        for o in _local_options():
            if o.engine_voice == name:
                return o.voice_id
        if _local_options():
            return _local_options()[0].voice_id
    if gemini_tts_available():
        name = (GEMINI_TTS_VOICE_PT or "Achernar").strip()
        return f"gemini:{name}"
    return "edge:pt-BR-ThalitaMultilingualNeural"


def default_voice_label() -> str:
    vid = default_voice_id()
    for o in all_tts_voice_options():
        if o.voice_id == vid:
            return o.label
    opts = all_tts_voice_options()
    return opts[0].label if opts else ""


def resolve_voice(voice: str) -> TtsVoiceOption:
    """
    Aceita `local:pf_dora`, `gemini:Achernar`, `edge:pt-BR-…` ou nome Edge legado.
    """
    raw = (voice or "").strip()
    if not raw:
        return resolve_voice(default_voice_id())

    if ":" in raw:
        provider, engine = raw.split(":", 1)
        provider = provider.strip().lower()
        engine = engine.strip()
        if provider == "local":
            for o in _local_options():
                if o.engine_voice == engine:
                    return o
            if local_tts_available():
                return TtsVoiceOption(
                    voice_id=f"local:{engine}",
                    label=engine,
                    provider="local",
                    engine_voice=engine,
                )
            raise ValueError(
                f"Voz local «{engine}» indisponível. Rode scripts/install_local_tts.sh"
            )
        if provider == "gemini":
            for o in _gemini_options():
                if o.engine_voice == engine:
                    return o
            if gemini_tts_available():
                return TtsVoiceOption(
                    voice_id=f"gemini:{engine}",
                    label=engine,
                    provider="gemini",
                    engine_voice=engine,
                )
            raise ValueError(
                f"Voz Gemini «{engine}» indisponível (configure GEMINI_API_KEY no .env)."
            )
        if provider == "edge":
            return TtsVoiceOption(
                voice_id=f"edge:{engine}",
                label=engine,
                provider="edge",
                engine_voice=engine,
            )

    for o in all_tts_voice_options():
        if o.voice_id == raw or o.engine_voice == raw or o.label == raw:
            return o

    return TtsVoiceOption(
        voice_id=f"edge:{raw}",
        label=raw,
        provider="edge",
        engine_voice=raw,
    )


def voice_id_from_label(label: str) -> str:
    lab = (label or "").strip()
    for o in all_tts_voice_options():
        if o.label == lab:
            return o.voice_id
    return resolve_voice(lab).voice_id
