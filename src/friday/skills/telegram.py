"""Етап 7: Надсилання повідомлень у Telegram через Telethon із кешем діалогів, нечітким пошуком і обов'язковим Confirm Gate."""

from __future__ import annotations

import difflib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from friday.config import PROJECT_ROOT
from friday.util.timing import measure_time

log = logging.getLogger("friday.skills.telegram")

DEFAULT_SESSION_PATH = PROJECT_ROOT / "friday_telegram.session"


@dataclass(slots=True, frozen=True)
class TelegramDialogEntry:
    """Кешований елемент діалогу Telegram."""

    entity_id: int | str
    title: str
    username: str = ""


def _stem_ukrainian_name(name: str) -> str:
    """Відсікає типові закінчення давального/знахідного відмінків українських імен (Олегу -> Олег, Марині -> Марин)."""
    cleaned = (name or "").strip().lower()
    for suffix in ("ові", "еві", "єві", "ам", "ям"):
        if len(cleaned) > len(suffix) + 2 and cleaned.endswith(suffix):
            return cleaned[: -len(suffix)]
    for suffix in ("у", "ю", "і", "ї", "а", "я", "о", "е"):
        if len(cleaned) > 3 and cleaned.endswith(suffix):
            return cleaned[:-1]
    return cleaned


def resolve_contact_fuzzy(
    query: str,
    dialogs: list[TelegramDialogEntry],
    min_score: float = 0.62,
) -> TelegramDialogEntry:
    """Знаходить найкращий контакт серед діалогів за нечітким збігом (із врахуванням відмінків імен)."""
    raw_q = (query or "").strip().lower().lstrip("@")
    if not raw_q:
        raise ValueError("Ім'я контакту Telegram не може бути порожнім")
    if not dialogs:
        raise LookupError("Список діалогів Telegram порожній")

    q_stem = _stem_ukrainian_name(raw_q)
    best_entry: TelegramDialogEntry | None = None
    best_score: float = 0.0

    for entry in dialogs:
        title_lower = entry.title.strip().lower()
        uname_lower = entry.username.strip().lower().lstrip("@")

        # Точний збіг за username або повною назвою
        if raw_q in {title_lower, uname_lower}:
            return entry

        tokens = [t for t in title_lower.replace("-", " ").split() if t]
        candidate_scores: list[float] = [
            difflib.SequenceMatcher(None, raw_q, title_lower).ratio(),
            difflib.SequenceMatcher(None, q_stem, _stem_ukrainian_name(title_lower)).ratio(),
        ]
        if uname_lower:
            candidate_scores.append(difflib.SequenceMatcher(None, raw_q, uname_lower).ratio())

        for tok in tokens:
            tok_stem = _stem_ukrainian_name(tok)
            if tok == raw_q or (len(q_stem) >= 3 and tok_stem == q_stem):
                candidate_scores.append(0.96)
            elif len(q_stem) >= 3 and (tok.startswith(q_stem) or q_stem.startswith(tok_stem)):
                candidate_scores.append(0.88)
            else:
                candidate_scores.append(difflib.SequenceMatcher(None, q_stem, tok_stem).ratio())

        score = max(candidate_scores)
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry is None or best_score < min_score:
        raise LookupError(
            f"Контакт «{query}» не знайдено серед діалогів Telegram (найкращий збіг: {best_score:.2f})"
        )
    return best_entry


def format_telegram_confirm_prompt(contact: str, text: str) -> str:
    """Формує текст toast-підтвердження згідно з Етапом 7 fridayplan.md."""
    display_contact = (contact or "").strip().capitalize()
    return f'{display_contact}: "{text}" — відправити? (Enter / Esc)'


class TelegramService:
    """Сервіс роботи з Telegram через Telethon із локальною сесією та кешуванням діалогів."""

    def __init__(
        self,
        api_id: str | int = "",
        api_hash: str = "",
        session_path: Path | None = None,
        cache_ttl_s: float = 300.0,
        dialogs_fetcher: Callable[[], Awaitable[list[TelegramDialogEntry]]] | None = None,
        message_sender: Callable[[int | str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.api_id = str(api_id or "").strip()
        self.api_hash = (api_hash or "").strip()
        self.session_path = session_path or DEFAULT_SESSION_PATH
        self.cache_ttl_s = cache_ttl_s
        self._dialogs_fetcher = dialogs_fetcher
        self._message_sender = message_sender

        self._dialogs_cache: list[TelegramDialogEntry] = []
        self._cache_timestamp: float = 0.0
        self._client: Any | None = None

    async def _ensure_telethon_client(self) -> Any:
        """Ліниво створює та підключає Telethon-клієнт лише за наявності ключів."""
        if self._client is not None:
            return self._client

        if not self.api_id or not self.api_hash:
            raise RuntimeError("TELEGRAM_API_ID та TELEGRAM_API_HASH не налаштовані у .env")

        try:
            from telethon import TelegramClient  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Бібліотеку telethon не встановлено (встановіть: pip install telethon)"
            ) from exc

        client = TelegramClient(str(self.session_path), int(self.api_id), self.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(
                "Сесія Telegram не авторизована. Виконайте одноразовий вхід для створення локальної сесії."
            )
        self._client = client
        return client

    async def get_dialogs(self, force_refresh: bool = False) -> list[TelegramDialogEntry]:
        """Повертає список діалогів із кешу або завантажує їх через Telethon."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._dialogs_cache
            and (now - self._cache_timestamp) < self.cache_ttl_s
        ):
            return list(self._dialogs_cache)

        if self._dialogs_fetcher is not None:
            fetched = await self._dialogs_fetcher()
        else:
            client = await self._ensure_telethon_client()
            raw_dialogs = await client.get_dialogs(limit=150)
            fetched = []
            for d in raw_dialogs:
                ent = getattr(d, "entity", None)
                uname = str(getattr(ent, "username", "") or "")
                title = str(getattr(d, "name", "") or getattr(d, "title", "") or "").strip()
                if title:
                    fetched.append(
                        TelegramDialogEntry(
                            entity_id=getattr(ent, "id", title),
                            title=title,
                            username=uname,
                        )
                    )

        self._dialogs_cache = list(fetched)
        self._cache_timestamp = now
        return list(self._dialogs_cache)

    async def send_confirmed_message(
        self,
        contact: str,
        text: str,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Надсилає повідомлення знайденому контакту ВИКЛЮЧНО якщо confirmed=True."""
        if not confirmed:
            raise PermissionError(
                "Відправка повідомлення в Telegram заблокована: обов'язкове підтвердження користувача (Confirm Gate)."
            )

        cleaned_text = (text or "").strip()
        if not cleaned_text:
            raise ValueError("Текст повідомлення Telegram не може бути порожнім")

        with measure_time("telegram.send"):
            dialogs = await self.get_dialogs()
            matched = resolve_contact_fuzzy(contact, dialogs)

            if self._message_sender is not None:
                await self._message_sender(matched.entity_id, cleaned_text)
            else:
                client = await self._ensure_telethon_client()
                await client.send_message(matched.entity_id, cleaned_text)

            log.info("✉️ Надіслано повідомлення у Telegram контакту: %s", matched.title)
            return {
                "status": "sent",
                "contact_query": contact,
                "resolved_title": matched.title,
                "entity_id": matched.entity_id,
            }


class TelegramSendSkill:
    """Навичка telegram.send з обов'язковою перевіркою прапорця confirmed."""

    name = "telegram.send"

    def __init__(self, service: TelegramService) -> None:
        self.service = service

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        contact = str(args.get("contact", "")).strip()
        text = str(args.get("text", "")).strip()
        confirmed = bool(args.get("_confirmed", False))
        return await self.service.send_confirmed_message(
            contact=contact,
            text=text,
            confirmed=confirmed,
        )
