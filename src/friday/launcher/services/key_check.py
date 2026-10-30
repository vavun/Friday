"""Мінімальна перевірка валідності ключа Gemini API з обробкою офлайну та помилок авторизації."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True, frozen=True)
class KeyCheckResult:
    """Результат тестового запиту до Gemini API."""

    ok: bool
    status: str  # "valid" | "invalid_key" | "offline" | "error"
    message: str


def verify_gemini_api_key(
    api_key: str,
    model: str = "gemini-3.5-flash-lite",
    client_factory: Callable[[str], Any] | None = None,
) -> KeyCheckResult:
    """Виконує мінімальний тестовий запит до Gemini API для перевірки ключа (без логування ключа)."""
    cleaned = (api_key or "").strip()
    if not cleaned:
        return KeyCheckResult(
            ok=False,
            status="invalid_key",
            message="Ключ порожній. Введіть GEMINI_API_KEY.",
        )

    try:
        if client_factory is not None:
            client = client_factory(cleaned)
        else:
            from google import genai

            client = genai.Client(api_key=cleaned)

        from google.genai import types

        resp = client.models.generate_content(
            model=model,
            contents="ok",
            config=types.GenerateContentConfig(
                max_output_tokens=4,
                temperature=0.0,
            ),
        )
        _ = getattr(resp, "text", None)
        return KeyCheckResult(
            ok=True,
            status="valid",
            message="Ключ валідний — з'єднання з Gemini API успішне (OK).",
        )
    except Exception as exc:
        err_text = str(exc).lower()
        if any(
            token in err_text
            for token in (
                "api_key_invalid",
                "invalid api key",
                "permission_denied",
                "unauthenticated",
                "401",
                "403",
                "api key not valid",
            )
        ):
            return KeyCheckResult(
                ok=False,
                status="invalid_key",
                message="Невірний ключ Gemini API (401/403). Перевірте скопійований ключ у Google AI Studio.",
            )
        if any(
            token in err_text
            for token in (
                "connecterror",
                "connection",
                "name or service not known",
                "getaddrinfo failed",
                "timed out",
                "timeout",
                "network",
            )
        ):
            return KeyCheckResult(
                ok=False,
                status="offline",
                message="Немає підключення до мережі або перевищено час очікування (Офлайн).",
            )
        return KeyCheckResult(
            ok=False,
            status="error",
            message=f"Помилка перевірки ключа: {type(exc).__name__}",
        )


def verify_explabs_api_key(
    api_key: str,
    model: str = "clef",
    base_url: str = "https://api.experientiallabs.ai/v1",
    client_factory: Callable[[str], Any] | None = None,
) -> KeyCheckResult:
    """Виконує мінімальний тестовий запит до Experiential Labs Clef API для перевірки EXPLABS_API_KEY."""
    cleaned = (api_key or "").strip()
    if not cleaned:
        return KeyCheckResult(
            ok=False,
            status="invalid_key",
            message="Ключ порожній. Введіть EXPLABS_API_KEY.",
        )

    try:
        if client_factory is not None:
            client = client_factory(cleaned)
        else:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=cleaned, timeout=8.0)

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ok"}],
            max_tokens=4,
            temperature=0.0,
        )
        _ = getattr(resp, "choices", None)
        return KeyCheckResult(
            ok=True,
            status="valid",
            message="Ключ EXPLABS_API_KEY валідний — з'єднання з моделлю Clef успішне (OK).",
        )
    except Exception as exc:
        err_text = str(exc).lower()
        if any(
            token in err_text
            for token in (
                "invalid_api_key",
                "invalid api key",
                "authentication",
                "unauthorized",
                "401",
                "403",
            )
        ):
            return KeyCheckResult(
                ok=False,
                status="invalid_key",
                message="Невірний ключ EXPLABS_API_KEY (401/403). Перевірте ключ Experiential Labs.",
            )
        if any(
            token in err_text
            for token in (
                "connecterror",
                "connection",
                "name or service not known",
                "getaddrinfo failed",
                "timed out",
                "timeout",
                "network",
            )
        ):
            return KeyCheckResult(
                ok=False,
                status="offline",
                message="Немає підключення до мережі або перевищено час очікування Clef API.",
            )
        return KeyCheckResult(
            ok=False,
            status="error",
            message=f"Помилка перевірки ключа Clef: {type(exc).__name__}",
        )
