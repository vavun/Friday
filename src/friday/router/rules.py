"""Fast path: детерміновані правила та регулярні вирази для україномовних голосових команд."""

from __future__ import annotations

import re
from typing import Any

from friday.router.schema import CommandIntent
from friday.skills.apps import APP_NAME_SYNONYMS


# Словник відмінкових форм і синонімів цільових вікон / програм
TARGET_SYNONYMS: dict[str, str] = {
    "браузер": "browser",
    "браузера": "browser",
    "браузері": "browser",
    "хром": "browser",
    "хрому": "browser",
    "chrome": "browser",
    "google chrome": "browser",
    "browser": "browser",
    "телеграм": "telegram",
    "телеграму": "telegram",
    "телеграмі": "telegram",
    "тг": "telegram",
    "telegram": "telegram",
    "код": "code",
    "коду": "code",
    "вс код": "code",
    "vscode": "code",
    "vs code": "code",
    "редактор": "code",
    "редактора": "code",
    "редакторі": "code",
    "code": "code",
    "термінал": "terminal",
    "термінала": "terminal",
    "терміналі": "terminal",
    "консоль": "terminal",
    "консолі": "terminal",
    "powershell": "terminal",
    "cmd": "terminal",
    "terminal": "terminal",
    "це вікно": "active",
    "активне вікно": "active",
    "поточне вікно": "active",
    "вікно": "active",
}

MONITOR_WORDS: dict[str, int] = {
    "перший": 1,
    "першому": 1,
    "основний": 1,
    "основному": 1,
    "головний": 1,
    "головному": 1,
    "1": 1,
    "другий": 2,
    "другому": 2,
    "інший": 2,
    "іншому": 2,
    "сусідній": 2,
    "сусідньому": 2,
    "правий": 2,
    "правому": 2,
    "2": 2,
    "третій": 3,
    "третьому": 3,
    "лівий": 3,
    "лівому": 3,
    "3": 3,
}

SITE_SYNONYMS: dict[str, str] = {
    "пайтон": "python",
    "пітон": "python",
    "python": "python",
    "фастапі": "fastapi",
    "fastapi": "fastapi",
    "пайдентік": "pydantic",
    "pydantic": "pydantic",
    "мдн": "mdn",
    "mdn": "mdn",
}


def normalize_command_text(raw: str) -> str:
    """Нормалізує текст команди: нижній регістр, апострофи, зайві пробіли та кінцева пунктуація."""
    text = (raw or "").strip()
    text = text.replace("’", "'").replace("`", "'").replace("ʼ", "'")
    text = text.lower()
    text = re.sub(r"[!?.,;]+$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def resolve_target_name(
    raw_target: str,
    custom_synonyms: dict[str, str] | None = None,
) -> str:
    """Нормалізує назву цільового вікна або програми за словником синонімів."""
    cleaned = normalize_command_text(raw_target)
    cleaned = re.sub(r"^(вікно|програму|застосунок)\s+", "", cleaned).strip()
    if custom_synonyms and cleaned in custom_synonyms:
        return custom_synonyms[cleaned]
    if cleaned in TARGET_SYNONYMS:
        return TARGET_SYNONYMS[cleaned]
    if cleaned in APP_NAME_SYNONYMS:
        return APP_NAME_SYNONYMS[cleaned]
    # Просте усічення типових українських закінчень знахідного/родового відмінків
    for suffix in ("а", "я", "у", "ю", "і", "ї"):
        if len(cleaned) > 4 and cleaned.endswith(suffix):
            stem = cleaned[:-1]
            if stem in TARGET_SYNONYMS:
                return TARGET_SYNONYMS[stem]
            if stem in APP_NAME_SYNONYMS:
                return APP_NAME_SYNONYMS[stem]
    return cleaned or "active"


def _extract_monitor_index(text: str, default: int = 1) -> tuple[int, bool]:
    """Шукає згадку монітора у тексті; повертає (monitor_index, found)."""
    m = re.search(
        r"(?:на\s+)?(перш\w+|основн\w+|головн\w+|друг\w+|інш\w+|сусідн\w+|прав\w+|трет\w+|лів\w+|[123])\s+монітор\w*",
        text,
    )
    if m:
        word = m.group(1)
        if word in MONITOR_WORDS:
            return (MONITOR_WORDS[word], True)
        for key, idx in MONITOR_WORDS.items():
            if word.startswith(key[:4]):
                return (idx, True)

    m_num = re.search(r"монітор\w*\s+([123])\b", text)
    if m_num:
        return (int(m_num.group(1)), True)

    return (default, False)


def _extract_snap_mode(text: str, default: str = "maximize") -> tuple[str, bool]:
    """Визначає режим позиціонування вікна (left / right / center / maximize)."""
    if re.search(r"\b(ліворуч|вліво|зліва|на лівий бік|на ліву половину)\b", text):
        return ("left", True)
    if re.search(r"\b(праворуч|вправо|справа|на правий бік|на праву половину)\b", text):
        return ("right", True)
    if re.search(r"\b(по центру|в центр|у центрі|посередині)\b", text):
        return ("center", True)
    if re.search(r"\b(розгорни|на весь екран|максимізуй)\b", text):
        return ("maximize", True)
    return (default, False)


def match_fast_rule(
    raw_text: str,
    custom_apps: dict[str, str] | None = None,
    custom_synonyms: dict[str, str] | None = None,
) -> CommandIntent | None:
    """Намагається розпізнати команду детерміновано без виклику LLM (< 2 мс)."""
    orig = (raw_text or "").strip()
    norm = normalize_command_text(orig)
    if not norm:
        return None

    # 1. Керування режимом жестів (gesture.drag)
    if re.search(r"\b(вимкн\w*|зупин\w*|закри\w*)\s+(?:режим\s+)?жест\w*", norm):
        return CommandIntent(skill="gesture.drag", args={"action": "stop"}, confidence=0.98)
    if re.search(r"\b(режим\s+жест\w*|(?:увімкн\w*|ввімкн\w*|запуст\w*|активу\w*)\s+(?:режим\s+)?жест\w*)", norm):
        return CommandIntent(skill="gesture.drag", args={"action": "start"}, confidence=0.98)

    # 2. Відправка повідомлення у Telegram (telegram.send) — перевіряємо ПЕРЕД text.type
    m_tg = re.match(
        r"^(?:напиши|відправ|надішли|скинь)\s+([а-яіїєґa-z0-9_]+)\s+(?:в|у)\s+(?:телеграм\w*|тг|telegram)\s*(?:що|про те що|:)?\s*(.+)$",
        norm,
    )
    if not m_tg:
        m_tg = re.match(
            r"^(?:напиши|відправ|надішли|скинь)\s+(?:в|у)\s+(?:телеграм\w*|тг|telegram)\s+([а-яіїєґa-z0-9_]+)\s*(?:що|:)?\s*(.+)$",
            norm,
        )
    if m_tg:
        contact_raw = m_tg.group(1).strip()
        msg_text = m_tg.group(2).strip()
        if contact_raw and msg_text:
            return CommandIntent(
                skill="telegram.send",
                args={"contact": contact_raw, "text": msg_text},
                confidence=0.95,
                needs_confirm=True,
            )

    # 3. Введення тексту в активне поле (text.type)
    m_type_orig = re.match(
        r"^(?:напиши|надрукуй|введи(?:\s+текст)?|встав(?:\s+текст)?)\s*[:\-]?\s+(.+)$",
        orig,
        flags=re.IGNORECASE,
    )
    if m_type_orig and not re.search(r"\b(в|у)\s+(телеграм\w*|тг|telegram)\b", norm):
        payload = m_type_orig.group(1).strip()
        # Не перехоплюємо агентські команди типу "напиши скрипт / функцію / тест"
        if not re.match(r"^(скрипт|функцію|клас|тест|код|програму)\b", payload.lower()):
            return CommandIntent(
                skill="text.type",
                args={"text": payload},
                confidence=0.96,
            )

    # 4. Документація за помилкою (docs.lookup)
    if re.search(r"\b(документац\w*|поясни\s+помил[кц]\w*|розбери\s+помил[кц]\w*)\b", norm) and re.search(
        r"\b(помил[кц]\w*|error|exception|traceback|буфер\w*)\b", norm
    ):
        err_match = re.search(r"\b([A-Za-z]+(?:Error|Exception|Warning))\b", orig)
        args_doc: dict[str, Any] = {"source": "clipboard"}
        if err_match:
            args_doc["error"] = err_match.group(1)
        return CommandIntent(skill="docs.lookup", args=args_doc, confidence=0.96)

    # 5. Пошук у Google / на сайтах документації (web.search)
    m_web = re.match(
        r"^(?:знайди\s+(?:в|у)\s+(?:гугл\w*|google|інтернет\w*|мереж\w*)|пошукай\s+(?:в|у)\s+(?:гугл\w*|google|інтернет\w*)|загугли|погугли|шукай)\s+(.+)$",
        orig,
        flags=re.IGNORECASE,
    )
    if m_web:
        query_str = m_web.group(1).strip()
        args_web: dict[str, Any] = {"query": query_str}
        for site_word, site_key in SITE_SYNONYMS.items():
            if re.search(rf"\b{re.escape(site_word)}\b", query_str.lower()):
                args_web["site"] = site_key
                break
        return CommandIntent(skill="web.search", args=args_web, confidence=0.96)

    # 6. Агентські кодерські задачі (agent.run)
    if re.match(
        r"^(?:відрефактор\w*|рефактор\w*|перепиши\s+файл|оптимізуй\s+файл|виправ\s+баг\s+у|напиши\s+(?:скрипт|функцію|клас|юніт-?тести|тести)|проаналізуй\s+код)\b",
        norm,
    ):
        return CommandIntent(
            skill="agent.run",
            args={"prompt": orig},
            confidence=0.93,
            needs_confirm=True,
        )

    # 7. Переміщення вікна між моніторами або снап (window.move)
    m_move = re.match(
        r"^(?:перенеси|перекинь|відправ|перемісти|посунь|кинь)\s+(.+?)\s+на\s+(?:перш\w+|основн\w+|головн\w+|друг\w+|інш\w+|сусідн\w+|прав\w+|трет\w+|лів\w+|[123])\s+монітор\w*(.*)$",
        norm,
    )
    if m_move:
        target = resolve_target_name(m_move.group(1), custom_synonyms)
        monitor_idx, _ = _extract_monitor_index(norm, default=2)
        snap_mode, _ = _extract_snap_mode(m_move.group(2), default="maximize")
        return CommandIntent(
            skill="window.move",
            args={"target": target, "monitor": monitor_idx, "mode": snap_mode},
            confidence=0.96,
        )

    m_snap = re.match(
        r"^(?:постав|прикріпи|притисни|розмісти|перемісти|зсунь)\s+(.+?)\s+(ліворуч|вліво|зліва|на лівий бік|на ліву половину|праворуч|вправо|справа|на правий бік|на праву половину|по центру|в центр|у центрі|посередині)(.*)$",
        norm,
    )
    if m_snap:
        target = resolve_target_name(m_snap.group(1), custom_synonyms)
        snap_mode, _ = _extract_snap_mode(m_snap.group(2), default="center")
        monitor_idx, _ = _extract_monitor_index(m_snap.group(3), default=1)
        return CommandIntent(
            skill="window.move",
            args={"target": target, "monitor": monitor_idx, "mode": snap_mode},
            confidence=0.95,
        )

    # 8. Зміна розміру вікна (window.resize)
    m_resize = re.match(
        r"^(збільш\w*|зменш\w*|розгорн\w*|максимізу\w*|віднов\w*|нормалізу\w*)\s*(.*)$",
        norm,
    )
    if m_resize:
        verb = m_resize.group(1)
        rest_target = m_resize.group(2).strip()
        # Переконуємось, що це не "розгорни на другому моніторі" з переміщенням
        if verb.startswith("збільш"):
            mode = "bigger"
        elif verb.startswith("зменш"):
            mode = "smaller"
        elif verb.startswith(("розгорн", "максимізу")):
            mode = "maximize"
        else:
            mode = "restore"

        target = resolve_target_name(rest_target, custom_synonyms) if rest_target else "active"
        return CommandIntent(
            skill="window.resize",
            args={"target": target, "mode": mode},
            confidence=0.96,
        )

    # 9. Фокус на вікні (window.focus)
    m_focus = re.match(
        r"^(?:перейди\s+(?:в|у|до)|переключись\s+на|перемкнись\s+на|сфокусуй(?:ся\s+на)?|активуй\s+вікно|покажи\s+вікно)\s+(.+)$",
        norm,
    )
    if m_focus:
        target = resolve_target_name(m_focus.group(1), custom_synonyms)
        return CommandIntent(
            skill="window.focus",
            args={"target": target},
            confidence=0.96,
        )

    # 10. Запуск програми з білого списку (app.launch)
    m_launch = re.match(r"^(?:відкрий|запусти|стартуй|увімкни|ввімкни)\s+(.+)$", norm)
    if m_launch:
        raw_app = m_launch.group(1).strip()
        app_key = resolve_target_name(raw_app, custom_synonyms)
        allowed_keys = set(APP_NAME_SYNONYMS.values()) | {"browser", "telegram", "code"}
        if custom_apps:
            allowed_keys |= {k.lower() for k in custom_apps}
        if app_key in allowed_keys or raw_app in allowed_keys:
            return CommandIntent(
                skill="app.launch",
                args={"name": app_key if app_key in allowed_keys else raw_app},
                confidence=0.96,
            )

    return None
