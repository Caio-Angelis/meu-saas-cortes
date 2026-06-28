"""Chat completions na API Groq (limite de concorrência + retries)."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from groq import BadRequestError, Groq, RateLimitError

from app.core.config import GROQ_API_KEY
from app.core.limits import groq_limiter, groq_retry_policy, with_retries

_log = logging.getLogger("groq_chat")

_client: Groq | None = None


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
    model: str = "llama-3.3-70b-versatile",
) -> str | None:
    """
    Uma mensagem user; modelo fixo llama-3.3-70b-versatile (igual ao restante do app).
    retry_label aparece nos avisos de rate limit (ex.: "Groq", "legenda TikTok").
    """

    def _do() -> str | None:
        with groq_limiter.acquire():
            response = _get_client().chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
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
