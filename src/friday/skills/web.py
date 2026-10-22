"""Вебпошук у Google (з підтримкою site:) та відкриття відомих сайтів із конфігурації."""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
import webbrowser
from typing import Any, Callable

from friday.util.timing import measure_time

log = logging.getLogger("friday.skills.web")

DEFAULT_DOCS_SITES: dict[str, str] = {
    "python": "docs.python.org",
    "fastapi": "fastapi.tiangolo.com",
    "pydantic": "docs.pydantic.dev",
    "mdn": "developer.mozilla.org",
    "github": "github.com",
    "гітхаб": "github.com",
    "stackoverflow": "stackoverflow.com",
    "youtube": "youtube.com",
    "ютуб": "youtube.com",
    "ютюб": "youtube.com",
    "google": "google.com",
    "гугл": "google.com",
    "chatgpt": "chatgpt.com",
    "чат гпт": "chatgpt.com",
    "gmail": "mail.google.com",
    "пошта": "mail.google.com",
}


def resolve_site_domain(site: str | None, docs_sites: dict[str, str] | None = None) -> str:
    """Перетворює коротку назву сайту (напр. 'fastapi') на домен ('fastapi.tiangolo.com')."""
    if not site:
        return ""
    cleaned = site.strip().lower()
    merged = dict(DEFAULT_DOCS_SITES)
    if docs_sites:
        for k, v in docs_sites.items():
            merged[k.lower()] = v.strip()
    if cleaned in merged:
        return merged[cleaned]
    if "." in cleaned and " " not in cleaned:
        return cleaned.removeprefix("https://").removeprefix("http://").strip("/")
    return ""


def build_google_search_url(
    query: str,
    site: str | None = None,
    docs_sites: dict[str, str] | None = None,
) -> str:
    """Формує безпечний URL пошуку Google із фільтром site:<домен> за потреби."""
    cleaned_q = (query or "").strip()
    if not cleaned_q:
        raise ValueError("Пошуковий запит не може бути порожнім")

    domain = resolve_site_domain(site, docs_sites)
    full_query = f"{cleaned_q} site:{domain}" if domain and f"site:{domain}" not in cleaned_q else cleaned_q
    encoded = urllib.parse.quote_plus(full_query)
    return f"https://www.google.com/search?q={encoded}"


class WebBrowserService:
    """Сервіс побудови пошукових запитів та відкриття сайтів у браузері."""

    def __init__(
        self,
        docs_sites: dict[str, str] | None = None,
        open_url_fn: Callable[[str], Any] | None = None,
    ) -> None:
        self.docs_sites = docs_sites or dict(DEFAULT_DOCS_SITES)
        self._open_url_fn = open_url_fn or webbrowser.open

    def search_sync(self, query: str, site: str | None = None) -> dict[str, Any]:
        """Синхронно будує URL пошуку і відкриває його в браузері за замовчуванням."""
        with measure_time("web.search"):
            url = build_google_search_url(query, site=site, docs_sites=self.docs_sites)
            self._open_url_fn(url)
            log.info("🌐 Відкрито пошук у браузері: %s", url)
            return {"url": url, "query": query, "site": site or "", "status": "opened"}

    def open_site_sync(self, site_key: str) -> dict[str, Any]:
        """Відкриває відомий сайт із конфігу за його ключем або доменом."""
        with measure_time("web.open"):
            domain = resolve_site_domain(site_key, self.docs_sites)
            if not domain:
                raise KeyError(f"Невідомий сайт '{site_key}' у списку docs_sites")
            url = f"https://{domain}"
            self._open_url_fn(url)
            log.info("🌐 Відкрито сайт документації: %s", url)
            return {"url": url, "site": site_key, "status": "opened"}


class WebSearchSkill:
    """Навичка web.search."""

    name = "web.search"

    def __init__(self, service: WebBrowserService | None = None) -> None:
        self.service = service or WebBrowserService()

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        open_site = args.get("open_site")
        if open_site:
            return await asyncio.to_thread(self.service.open_site_sync, str(open_site).strip())
        query = str(args.get("query", "")).strip()
        site = args.get("site")
        site_str = str(site).strip() if site else None
        return await asyncio.to_thread(self.service.search_sync, query, site_str)
