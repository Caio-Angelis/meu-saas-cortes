"""Chat completions na API Groq (limite de concorrência + retries).

Com LOCAL_LLM_BASE_URL/LOCAL_LLM_MODEL configurados, as chamadas vão primeiro
para um servidor OpenAI-compatível local (ex.: llama-server) e só caem para o
Groq quando o servidor local falha.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from groq import BadRequestError, Groq, RateLimitError

from app.core.config import (
    GROQ_API_KEY,
    GROQ_CHAT_MODEL,
    LOCAL_LLM_API_KEY,
    LOCAL_LLM_BASE_URL,
    LOCAL_LLM_MODEL,
    LOCAL_LLM_TIMEOUT_SEC,
)
from app.core.limits import groq_limiter, groq_retry_policy, with_retries

_log = logging.getLogger("groq_chat")

_client: Groq | None = None
_local_disabled_until = 0.0
_local_state_lock = threading.Lock()


def _local_failure_cooldown_sec() -> float:
    raw = (os.getenv("LOCAL_LLM_FAILURE_COOLDOWN_SEC") or "60").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 60.0


def local_llm_enabled() -> bool:
    if not (LOCAL_LLM_BASE_URL and LOCAL_LLM_MODEL):
        return False
    with _local_state_lock:
        return time.monotonic() >= _local_disabled_until


def _temporarily_disable_local(exc: Exception) -> None:
    global _local_disabled_until
    cooldown = _local_failure_cooldown_sec()
    with _local_state_lock:
        _local_disabled_until = time.monotonic() + cooldown
    _log.warning(
        "LLM local indisponível (%s); usando Groq por %.0fs antes de tentar o local novamente.",
        exc,
        cooldown,
    )


def _groq_model_options(model: str) -> dict[str, str]:
    """Opções conservadoras para modelos de raciocínio que precisam devolver JSON."""

    if not (model or "").casefold().startswith("openai/gpt-oss-"):
        return {}
    effort = (os.getenv("GROQ_REASONING_EFFORT") or "low").strip().casefold()
    if effort not in {"low", "medium", "high"}:
        effort = "low"
    return {"reasoning_effort": effort}


def _local_chat(
    prompt: str,
    *,
    temperature: float,
    max_tokens: int,
) -> str | None:
    """Chamada única ao servidor local; qualquer falha sobe para o caller decidir."""
    payload = {
        "model": LOCAL_LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Content-Type": "application/json"}
    if LOCAL_LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LOCAL_LLM_API_KEY}"
    req = urllib.request.Request(
        f"{LOCAL_LLM_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LOCAL_LLM_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 400 por chat_template_kwargs não suportado: tenta sem a chave extra.
        if e.code == 400 and "chat_template_kwargs" in json.dumps(payload):
            payload.pop("chat_template_kwargs", None)
            req = urllib.request.Request(
                f"{LOCAL_LLM_BASE_URL}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=LOCAL_LLM_TIMEOUT_SEC) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        else:
            raise
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("Resposta do LLM local sem choices")
    return choices[0].get("message", {}).get("content")


def _groq_timeout_sec() -> float:
    raw = (os.getenv("GROQ_HTTP_TIMEOUT_SEC") or "").strip()
    if not raw:
        return 180.0
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 180.0


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY, timeout=_groq_timeout_sec())
    return _client


def groq_user_message_text(
    prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    none_as_empty: bool,
    retry_label: str,
    bad_request_runtime: Callable[[BadRequestError], RuntimeError],
    rate_limit_message: str,
    model: str = GROQ_CHAT_MODEL,
) -> str | None:
    """
    Uma mensagem user; usa o modelo Groq configurado em GROQ_CHAT_MODEL por padrão.
    Com LLM local configurado, tenta primeiro o servidor local e usa o Groq como fallback.
    retry_label aparece nos avisos de rate limit (ex.: "Groq", "legenda TikTok").
    """

    def _do() -> str | None:
        if local_llm_enabled():
            try:
                return _local_chat(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                _temporarily_disable_local(e)
        with groq_limiter.acquire():
            response = _get_client().chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                **_groq_model_options(model),
            )
            content = response.choices[0].message.content
            if none_as_empty:
                return content or ""
            return content

    def _retryable(e: Exception) -> bool:
        return isinstance(e, RateLimitError)

    def _on_retry(i: int, sleep_s: float, _e: Exception) -> None:
        _log.warning(
            "Rate limit (%s). Tentativa %s em %.1fs...",
            retry_label,
            i + 1,
            sleep_s,
        )

    try:
        return with_retries(_do, policy=groq_retry_policy, should_retry=_retryable, on_retry=_on_retry)
    except BadRequestError as e:
        raise bad_request_runtime(e) from e
    except RateLimitError as e:
        raise RuntimeError(rate_limit_message) from e
