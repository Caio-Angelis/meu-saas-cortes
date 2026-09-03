"""Testes do adaptador Groq sem chamadas de rede."""

from __future__ import annotations

from types import SimpleNamespace

from app.ai_integrations import groq_chat as gc


def test_gpt_oss_uses_low_reasoning_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(gc, "local_llm_enabled", lambda: False)
    monkeypatch.setattr(gc, "_get_client", lambda: fake_client)
    monkeypatch.delenv("GROQ_REASONING_EFFORT", raising=False)

    result = gc.groq_user_message_text(
        "Responda somente OK.",
        temperature=0.0,
        max_tokens=32,
        none_as_empty=False,
        retry_label="teste",
        bad_request_runtime=lambda exc: RuntimeError(str(exc)),
        rate_limit_message="rate limit",
        model="openai/gpt-oss-20b",
    )

    assert result == "OK"
    assert captured["reasoning_effort"] == "low"


def test_non_reasoning_model_does_not_receive_gpt_oss_option(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(gc, "local_llm_enabled", lambda: False)
    monkeypatch.setattr(gc, "_get_client", lambda: fake_client)

    result = gc.groq_user_message_text(
        "Responda somente OK.",
        temperature=0.0,
        max_tokens=32,
        none_as_empty=False,
        retry_label="teste",
        bad_request_runtime=lambda exc: RuntimeError(str(exc)),
        rate_limit_message="rate limit",
        model="qwen/qwen3.6-27b",
    )

    assert result == "OK"
    assert "reasoning_effort" not in captured
