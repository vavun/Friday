"""Fast path: детерміновані правила та регулярні вирази для україномовних голосових команд.

Підтримує складні комбіновані промпти (ланцюжки дій), прямі операції з файлами/папками (file.create, file.open),
роботу з моніторами, вікнами, програмами та введенням тексту без звернення до LLM.
"""

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
    "його": "active",
    "її": "active",
    "це": "active",
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
    "гітхаб": "github",
    "github": "github",
    "ютуб": "youtube",
    "ютюб": "youtube",
    "youtube": "youtube",
    "гугл": "google",
    "google": "google",
    "чатгпт": "chatgpt",
    "chatgpt": "chatgpt",
}

# Дієслова початку самостійних дій для розбиття складених речень
ACTION_VERBS_PATTERN = (
    r"(?:відкри\w*|запуст\w*|старту\w*|створ\w*|перенес\w*|перекин\w*|відправ\w*|"
    r"постав\w*|прикріп\w*|притисн\w*|розгорн\w*|збільш\w*|зменш\w*|напиш\w*|надрукуй\w*|"
    r"введ\w*|встав\w*|знайд\w*|пошукай\w*|загугл\w*|погугл\w*|шукай\w*|перейд\w*|сфокусуй\w*|"
    r"активуй\w*|покаж\w*|вимкн\w*|зупин\w*)"
)

# Попередньо скомпільовані патерни для Fast Path
_RE_PUNCT_QUOTES = re.compile(r'["«»“”„‟]')
_RE_PUNCT_END = re.compile(r"[!?.,;]+$")
_RE_WHISPER_VSCODE_1 = re.compile(r"\bнівс-?код\b")
_RE_WHISPER_VSCODE_2 = re.compile(r"\bв\s+ні\s+(?=vs|вс|код|блокнот)")
_RE_SPACES = re.compile(r"\s+")
_RE_CLEAN_PREFIX = re.compile(r"^(вікно|програму|застосунок)\s+")

_RE_MONITOR_EXPLICIT = re.compile(
    r"(?:на\s+)?(перш\w+|основн\w+|головн\w+|друг\w+|інш\w+|сусідн\w+|прав\w+|трет\w+|лів\w+|[123])\s+монітор\w*"
)
_RE_MONITOR_NUM = re.compile(r"монітор\w*\s+([123])\b")

_RE_SNAP_LEFT = re.compile(r"\b(ліворуч|вліво|зліва|на лівий бік|на ліву половину|в ліву сторону|у ліву сторону)\b")
_RE_SNAP_RIGHT = re.compile(r"\b(праворуч|вправо|справа|на правий бік|на праву половину|в праву сторону|у праву сторону)\b")
_RE_SNAP_CENTER = re.compile(r"\b(по центру|в центр|у центрі|посередині)\b")
_RE_SNAP_MAX = re.compile(r"\b(розгорни|на весь екран|максимізуй|зроби на весь екран)\b")

_RE_EXPLICIT_SPLIT = re.compile(
    r"\s*(?:[,;]\s*)?(?:і\s+потім|а\s+потім|після\s+цього|потім|далі|також)\s+",
    re.IGNORECASE,
)
_RE_COMPOUND_BOUNDARY = re.compile(
    rf"(?:[,;]\s*(?:(?:і|та|й|а)\s+)?|\s+(?:і|та|й|а)\s+|\s+)(?={ACTION_VERBS_PATTERN}\b)",
    re.IGNORECASE,
)

_RE_GESTURE_STOP = re.compile(r"\b(вимкн\w*|зупин\w*|закри\w*)\s+(?:режим\s+)?жест\w*")
_RE_GESTURE_START = re.compile(r"\b(режим\s+жест\w*|(?:увімкн\w*|ввімкн\w*|запуст\w*|активу\w*)\s+(?:режим\s+)?жест\w*)")

_RE_CREATE_FOLDER = re.compile(
    r"^(?:створи\w*|зроби\w*)\s+(?:нову\s+|новий\s+)?(?:папку|директорію|каталог)\s+(?:під\s+назвою\s+|з\s+назвою\s+)?([^\s]+|\"[^\"]+\")"
    r"(?:\s+(?:на|в|у)\s+(робочому\s+столі|робочий\s+стіл|десктопі|десктоп|завантаженнях|завантаження|документах|документи|проєкті|проєкт|проекті|проект|[^\s]+))?"
    r"(?:\s+(?:і|та|й)?\s*відкри\w*(?:\s+(?:її|його|цю\s+папку))?(?:\s+(?:в|у|через)\s+(.+))?)?$"
)

_RE_CREATE_FILE = re.compile(
    r"^(?:створи\w*|зроби\w*)\s+(?:новий\s+|нову\s+)?(?:(пайтон|пітон|текстовий|маркдаун|джейсон|html|css|js)\s+)?(?:файл|документ)\s+(?:під\s+назвою\s+|з\s+назвою\s+)?([^\s]+|\"[^\"]+\")"
    r"(?:\s+(?:на|в|у)\s+(робочому\s+столі|робочий\s+стіл|десктопі|десктоп|завантаженнях|завантаження|документах|документи|проєкті|проєкт|проекті|проект|[^\s]+))?"
    r"(?:\s+(?:і|та|й)?\s*відкри\w*(?:\s+(?:його|її|цей\s+файл))?(?:\s+(?:в|у|через)\s+(\w+))?)?$"
)

_RE_OPEN_DIR = re.compile(
    r"^(?:відкри\w*|покажи)\s+(?:папку|директорію|каталог)\s+(.+?)(?:\s+(?:в|у|через)\s+(\w+))?$"
)

_RE_OPEN_FILE_1 = re.compile(
    r"^(?:відкри\w*|покажи)\s+(?:файл\w*|документ(?:а|у|ом|і)?)\s+(.+?)(?:\s+(?:на|в|у)\s+(робочому\s+столі|робочий\s+стіл|десктопі|десктоп|завантаженнях|завантаження|документах|документи|[^\s]+))?(?:\s+(?:в|у|через)\s+(\w+))?$"
)

_RE_OPEN_FILE_2 = re.compile(
    r"^(?:відкри\w*|покажи)\s+([a-zA-Z0-9_\-а-яіїєґ]+\.[a-zA-Z0-9]+)(?:\s+(?:на|в|у)\s+(робочому\s+столі|робочий\s+стіл|десктопі|десктоп|завантаженнях|завантаження|документах|документи))?(?:\s+(?:в|у|через)\s+(\w+))?$"
)

_RE_OPEN_PREV = re.compile(
    r"^(?:відкри\w*|покажи)\s+(?:(?:його|її|це|цей\s+файл|цю\s+папку)\s+(?:в|у|через)\s+(.+)|(?:його|її|це|цей\s+файл|цю\s+папку)|(?:в|у|через|в\s+ній|у\s+ній)\s+(.+))$"
)
_RE_OPEN_PREV_PRONOUNS = re.compile(r"^(?:ній|ньому|цьому|цій)\s+")

_RE_KNOWN_DIR = re.compile(
    r"^(?:відкри\w*|покажи)\s+(робочий\s+стіл|десктоп|завантаження|документи|проєкт|проект|папку\s+проєкту|папку\s+проекту)(?:\s+(?:в|у|через)\s+(\w+))?$"
)

_RE_TG_1 = re.compile(
    r"^(?:напиш\w*|відправ\w*|надішл\w*|скин\w*)\s+([а-яіїєґa-z0-9_]+(?:\s+[а-яіїєґa-z0-9_]+){0,2})\s+(?:в|у)\s+(?:телеграм\w*|тг|telegram)\s*(?:що|про те що|:)?\s*(.+)$"
)
_RE_TG_2 = re.compile(
    r"^(?:напиш\w*|відправ\w*|надішл\w*|скин\w*)\s+(?:в|у)\s+(?:телеграм\w*|тг|telegram)\s+([а-яіїєґa-z0-9_]+(?:\s+[а-яіїєґa-z0-9_]+){0,1})\s*(?:що|про те що|:)\s*(.+)$"
)
_RE_TG_3 = re.compile(
    r"^(?:напиш\w*|відправ\w*|надішл\w*|скин\w*)\s+(?:в|у)\s+(?:телеграм\w*|тг|telegram)\s+([а-яіїєґa-z0-9_]+)\s+(.+)$"
)

_RE_SITE = re.compile(
    r"^(?:відкри\w*|перейди\s+на)\s+(?:сторінку|сайт|веб-сайт)?\s*(youtube|ютуб|ютюб|github|гітхаб|google|гугл|chatgpt|чат\s*гпт|пошту|gmail|mail)$"
)

_RE_TYPE_ORIG = re.compile(
    r"^(?:напиши|надрукуй|введи(?:\s+текст)?|встав(?:\s+текст)?)\s*[:\-]?\s+(.+)$",
    re.IGNORECASE,
)
_RE_IN_TG = re.compile(r"\b(в|у)\s+(телеграм\w*|тг|telegram)\b")
_RE_CODE_KEYWORD = re.compile(r"^(скрипт|функцію|клас|тест|код|програму)\b")

_RE_DOCS_TRIGGER = re.compile(r"\b(документац\w*|поясни\s+помил[кц]\w*|розбери\s+помил[кц]\w*)\b")
_RE_DOCS_KEYWORD = re.compile(r"\b(помил[кц]\w*|error|exception|traceback|буфер\w*)\b")
_RE_EXCEPTION_NAME = re.compile(r"\b([A-Za-z]+(?:Error|Exception|Warning))\b")

_RE_WEB = re.compile(
    r"^(?:знайди\s+(?:в|у)\s+(?:гугл\w*|google|інтернет\w*|мереж\w*)|пошукай\s+(?:в|у)\s+(?:гугл\w*|google|інтернет\w*)|загугли|погугли|шукай)\s+(.+)$",
    re.IGNORECASE,
)

_RE_AGENT = re.compile(
    r"^(?:відрефактор\w*|рефактор\w*|перепиши\s+файл|оптимізуй\s+файл|виправ\s+баг\s+у|напиши\s+(?:скрипт|функцію|клас|юніт-?тести|тести)|проаналізуй\s+код)\b"
)

_RE_MOVE_EXPLICIT = re.compile(
    r"^(?:перенес\w*|перекин\w*|відправ\w*|переміст\w*|посун\w*|кин\w*)"
    r"(?:\s+(.*?))?"
    r"\s+на\s+((?:перш\w+|основн\w+|головн\w+|друг\w+|інш\w+|сусідн\w+|прав\w+|трет\w+|лів\w+|[123])\s+монітор\w*)"
    r"(?:\s+(.*))?$"
)

_RE_MOVE_BARE = re.compile(
    r"^(?:перенес\w*|перекин\w*|відправ\w*|переміст\w*|посун\w*|кин\w*)(?:\s+(.+))?$"
)
_RE_MOVE_CLEAN_WORDS = re.compile(
    r"\b(ліворуч|вліво|зліва|на лівий бік|на ліву половину|в ліву сторону|у ліву сторону|праворуч|вправо|справа|на правий бік|на праву половину|в праву сторону|у праву сторону|по центру|в центр|у центрі|посередині|розгорни|на весь екран|максимізуй)\b"
)

_RE_SNAP = re.compile(
    r"^(?:постав\w*|прикріп\w*|притисн\w*|розміст\w*|переміст\w*|зсунь)\s*(.*?)\s*(?:в|у|на)?\s*(ліворуч|вліво|зліва|на лівий бік|на ліву половину|в ліву сторону|праворуч|вправо|справа|на правий бік|на праву половину|в праву сторону|по центру|в центр|у центрі|посередині)(.*)$"
)

_RE_RESIZE = re.compile(
    r"^(збільш\w*|зменш\w*|розгорн\w*|максимізу\w*|віднов\w*|нормалізу\w*)\s*(.*)$"
)

_RE_FOCUS = re.compile(
    r"^(?:перейди\s+(?:в|у|до)|переключись\s+на|перемкнись\s+на|сфокусуй(?:ся\s+на)?|активуй\s+вікно|покажи\s+вікно)\s+(.+)$"
)

_RE_LAUNCH = re.compile(r"^(?:відкри\w*|запуст\w*|старту\w*|увімкн\w*|ввімкн\w*|включ\w*)\s+(.+)$")


def normalize_command_text(raw: str) -> str:
    """Нормалізує текст команди: нижній регістр, апострофи, лапки, виправлення типових артефактів Whisper."""
    text = (raw or "").strip()
    text = text.replace("’", "'").replace("`", "'").replace("ʼ", "'")
    text = _RE_PUNCT_QUOTES.sub("", text)
    text = text.lower()
    text = _RE_PUNCT_END.sub("", text).strip()
    # Виправлення типових фонетичних злипань Whisper для розпізнавання технічних програм
    text = _RE_WHISPER_VSCODE_1.sub("в ній vs code", text)
    text = _RE_WHISPER_VSCODE_2.sub("в ній ", text)
    text = _RE_SPACES.sub(" ", text)
    return text


def resolve_target_name(
    raw_target: str,
    custom_synonyms: dict[str, str] | None = None,
) -> str:
    """Нормалізує назву цільового вікна або програми за словником синонімів."""
    cleaned = normalize_command_text(raw_target)
    cleaned = _RE_CLEAN_PREFIX.sub("", cleaned).strip()
    if custom_synonyms and cleaned in custom_synonyms:
        return custom_synonyms[cleaned]
    if cleaned in TARGET_SYNONYMS:
        return TARGET_SYNONYMS[cleaned]
    if cleaned in APP_NAME_SYNONYMS:
        return APP_NAME_SYNONYMS[cleaned]
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
    m = _RE_MONITOR_EXPLICIT.search(text)
    if m:
        word = m.group(1)
        if word in MONITOR_WORDS:
            return (MONITOR_WORDS[word], True)
        for key, idx in MONITOR_WORDS.items():
            if word.startswith(key[:4]):
                return (idx, True)

    m_num = _RE_MONITOR_NUM.search(text)
    if m_num:
        return (int(m_num.group(1)), True)

    return (default, False)


def _extract_snap_mode(text: str, default: str = "maximize") -> tuple[str, bool]:
    """Визначає режим позиціонування вікна (left / right / center / maximize)."""
    if _RE_SNAP_LEFT.search(text):
        return ("left", True)
    if _RE_SNAP_RIGHT.search(text):
        return ("right", True)
    if _RE_SNAP_CENTER.search(text):
        return ("center", True)
    if _RE_SNAP_MAX.search(text):
        return ("maximize", True)
    return (default, False)


def split_compound_commands(text: str) -> list[str]:
    """Розбиває складений промпт на послідовні під-команди за сполучниками та дієсловами дії."""
    raw = (text or "").strip()
    if not raw:
        return []

    # 1. Попереднє розділення за явними послідовними маркерами (і потім, а потім, після цього, далі)
    explicit_split = _RE_EXPLICIT_SPLIT.split(raw)

    segments: list[str] = []
    for part in explicit_split:
        part = part.strip()
        if not part:
            continue

        # 2. Поділ на межі: (кома/крапка з комою або сполучник "і/та/й/а") безпосередньо перед дієсловом дії
        sub_parts = _RE_COMPOUND_BOUNDARY.split(part)
        for sp in sub_parts:
            sp = sp.strip().strip(",;.")
            if sp:
                segments.append(sp)

    # Якщо розбиття дало 0 частин, повертаємо початковий рядок
    return segments or [raw]


def _match_single_action(
    orig: str,
    norm: str,
    custom_apps: dict[str, str] | None = None,
    custom_synonyms: dict[str, str] | None = None,
    previous_target: str = "active",
) -> CommandIntent | None:
    """Розпізнає одну неподільну дію серед усіх навичок FRIDAY."""
    # 1. Керування режимом жестів (gesture.drag)
    if _RE_GESTURE_STOP.search(norm):
        return CommandIntent(skill="gesture.drag", args={"action": "stop"}, confidence=0.98)
    if _RE_GESTURE_START.search(norm):
        return CommandIntent(skill="gesture.drag", args={"action": "start"}, confidence=0.98)

    # 2. Створення папки або файлу (file.create)
    m_create_folder = _RE_CREATE_FOLDER.match(norm)
    if m_create_folder:
        f_name = m_create_folder.group(1).strip("\"'")
        loc = m_create_folder.group(2)
        app_hint = m_create_folder.group(3)
        return CommandIntent(
            skill="file.create",
            args={
                "name": f_name,
                "item_type": "folder",
                "location": loc.strip() if loc else None,
                "open_after": True if "відкри" in norm else False,
                "app": app_hint.strip() if app_hint else None,
            },
            confidence=0.96,
        )

    m_create_file = _RE_CREATE_FILE.match(norm)
    if m_create_file:
        kind = m_create_file.group(1)
        f_name = m_create_file.group(2).strip("\"'")
        loc = m_create_file.group(3)
        app_hint = m_create_file.group(4)
        return CommandIntent(
            skill="file.create",
            args={
                "name": f_name,
                "item_type": "file",
                "file_kind": kind.strip() if kind else None,
                "location": loc.strip() if loc else None,
                "open_after": True if "відкри" in norm else False,
                "app": app_hint.strip() if app_hint else None,
            },
            confidence=0.96,
        )

    # 3. Відкриття папки або файлу (file.open)
    m_open_dir = _RE_OPEN_DIR.match(norm)
    if m_open_dir:
        t_name = m_open_dir.group(1).strip()
        app_hint = m_open_dir.group(2)
        return CommandIntent(
            skill="file.open",
            args={
                "target": t_name,
                "prefer_dir": True,
                "app": app_hint.strip() if app_hint else None,
            },
            confidence=0.96,
        )

    m_open_file = None
    if "документаці" not in norm:
        m_open_file = _RE_OPEN_FILE_1.match(norm)
        if not m_open_file:
            # Відкриття файлу з розширенням без обов'язкового слова "файл": наприклад "відкрий script.py в блокноті"
            m_open_file = _RE_OPEN_FILE_2.match(norm)
    if m_open_file:
        f_name = m_open_file.group(1).strip()
        loc = m_open_file.group(2)
        app_hint = m_open_file.group(3)
        return CommandIntent(
            skill="file.open",
            args={
                "target": f_name,
                "location": loc.strip() if loc else None,
                "prefer_dir": False,
                "app": app_hint.strip() if app_hint else None,
            },
            confidence=0.96,
        )

    # Відкриття раніше згаданого / створеного файлу у програмі (наприклад: "і відкрий в блокноті", "відкрий його в коді", "відкрив в ній vs code")
    m_open_prev = _RE_OPEN_PREV.match(norm)
    if m_open_prev:
        g1, g2 = m_open_prev.group(1), m_open_prev.group(2)
        raw_app = (g1 or g2 or "").strip()
        app_hint = _RE_OPEN_PREV_PRONOUNS.sub("", raw_app).strip()
        return CommandIntent(
            skill="file.open",
            args={
                "target": previous_target if previous_target and previous_target != "active" else "останній",
                "prefer_dir": None,
                "app": app_hint if app_hint else None,
            },
            confidence=0.96,
        )

    # Пряме відкриття системних папок: "відкрий робочий стіл", "відкрий завантаження", "відкрий документи", "відкрий проєкт"
    m_known_dir = _RE_KNOWN_DIR.match(norm)
    if m_known_dir:
        t_name = m_known_dir.group(1).strip()
        app_hint = m_known_dir.group(2)
        return CommandIntent(
            skill="file.open",
            args={
                "target": t_name,
                "prefer_dir": True,
                "app": app_hint.strip() if app_hint else None,
            },
            confidence=0.97,
        )

    # 4. Відправка повідомлення у Telegram (telegram.send)
    # Варіант 1: "напиши Олегу в телеграм що / : / текст"
    m_tg = _RE_TG_1.match(norm)
    if not m_tg:
        # Варіант 2: "напиши в телеграм Олегу: текст" або "напиши в телеграм Олегу що текст"
        m_tg = _RE_TG_2.match(norm)
    if not m_tg:
        # Варіант 3: "напиши в телеграм Олегу текст" (без розділювача, одне слово контакт і далі повідомлення)
        m_tg = _RE_TG_3.match(norm)
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

    # 5. Відкриття конкретного сайту (web.search direct site)
    m_site = _RE_SITE.match(norm)
    if m_site:
        site_raw = m_site.group(1).replace(" ", "")
        canon_site = SITE_SYNONYMS.get(site_raw, site_raw)
        return CommandIntent(
            skill="web.search",
            args={"open_site": canon_site, "query": canon_site},
            confidence=0.97,
        )

    # 6. Введення тексту в активне поле (text.type)
    m_type_orig = _RE_TYPE_ORIG.match(orig)
    if m_type_orig and not _RE_IN_TG.search(norm):
        payload = m_type_orig.group(1).strip()
        if not _RE_CODE_KEYWORD.match(payload.lower()):
            return CommandIntent(
                skill="text.type",
                args={"text": payload},
                confidence=0.96,
            )

    # 7. Документація за помилкою (docs.lookup)
    if _RE_DOCS_TRIGGER.search(norm) and _RE_DOCS_KEYWORD.search(norm):
        err_match = _RE_EXCEPTION_NAME.search(orig)
        args_doc: dict[str, Any] = {"source": "clipboard"}
        if err_match:
            args_doc["error"] = err_match.group(1)
        return CommandIntent(skill="docs.lookup", args=args_doc, confidence=0.96)

    # 8. Пошук у Google / на сайтах документації (web.search)
    m_web = _RE_WEB.match(orig)
    if m_web:
        query_str = m_web.group(1).strip()
        args_web: dict[str, Any] = {"query": query_str}
        for site_word, site_key in SITE_SYNONYMS.items():
            if re.search(rf"\b{re.escape(site_word)}\b", query_str.lower()):
                args_web["site"] = site_key
                break
        return CommandIntent(skill="web.search", args=args_web, confidence=0.96)

    # 9. Агентські кодерські задачі (agent.run)
    if _RE_AGENT.match(norm):
        return CommandIntent(
            skill="agent.run",
            args={"prompt": orig},
            confidence=0.93,
            needs_confirm=True,
        )

    # 10. Переміщення вікна між моніторами або снап (window.move)
    # Варіант 1: з явною вказівкою монітора ("перенеси хром на сусідній монітор ліворуч", "перенеси браузер на другий монітор")
    m_move = _RE_MOVE_EXPLICIT.match(norm)
    if m_move:
        raw_t = (m_move.group(1) or "").strip()
        mon_phrase = m_move.group(2)
        rest = m_move.group(3) or ""
        target = resolve_target_name(raw_t, custom_synonyms) if raw_t else previous_target
        monitor_idx, _ = _extract_monitor_index(mon_phrase, default=2)
        snap_mode, _ = _extract_snap_mode(rest, default="maximize")
        return CommandIntent(
            skill="window.move",
            args={"target": target, "monitor": monitor_idx, "mode": snap_mode},
            confidence=0.96,
        )

    # Варіант 2: без явної фрази "на ... монітор" ("перенеси браузер", "перенеси вікно ліворуч")
    m_move_bare = _RE_MOVE_BARE.match(norm)
    if m_move_bare:
        raw = (m_move_bare.group(1) or "").strip()
        snap_mode, has_snap = _extract_snap_mode(raw, default="maximize")
        cleaned_target = _RE_MOVE_CLEAN_WORDS.sub("", raw).strip()
        target = resolve_target_name(cleaned_target, custom_synonyms) if cleaned_target else previous_target
        return CommandIntent(
            skill="window.move",
            args={"target": target, "monitor": None, "mode": snap_mode},
            confidence=0.95,
        )

    m_snap = _RE_SNAP.match(norm)
    if m_snap:
        raw_t = m_snap.group(1).strip()
        target = resolve_target_name(raw_t, custom_synonyms) if raw_t else previous_target
        snap_mode, _ = _extract_snap_mode(m_snap.group(2), default="center")
        monitor_idx, _ = _extract_monitor_index(m_snap.group(3), default=1)
        return CommandIntent(
            skill="window.move",
            args={"target": target, "monitor": monitor_idx, "mode": snap_mode},
            confidence=0.95,
        )

    # 11. Зміна розміру вікна (window.resize)
    m_resize = _RE_RESIZE.match(norm)
    if m_resize:
        verb = m_resize.group(1)
        rest_target = m_resize.group(2).strip()
        if verb.startswith("збільш"):
            mode = "bigger"
        elif verb.startswith("зменш"):
            mode = "smaller"
        elif verb.startswith(("розгорн", "максимізу")):
            mode = "maximize"
        else:
            mode = "restore"

        target = resolve_target_name(rest_target, custom_synonyms) if rest_target else previous_target
        return CommandIntent(
            skill="window.resize",
            args={"target": target, "mode": mode},
            confidence=0.96,
        )

    # 12. Фокус на вікні (window.focus)
    m_focus = _RE_FOCUS.match(norm)
    if m_focus:
        target = resolve_target_name(m_focus.group(1), custom_synonyms)
        return CommandIntent(
            skill="window.focus",
            args={"target": target},
            confidence=0.96,
        )

    # 13. Запуск програми з білого списку (app.launch)
    m_launch = _RE_LAUNCH.match(norm)
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


def match_fast_rule(
    raw_text: str,
    custom_apps: dict[str, str] | None = None,
    custom_synonyms: dict[str, str] | None = None,
) -> CommandIntent | None:
    """Розпізнає одиночну або складену (ланцюжок дій) команду детерміновано без виклику LLM."""
    orig = (raw_text or "").strip()
    norm = normalize_command_text(orig)
    if not norm:
        return None

    # 1. Перевіряємо, чи фраза містить кілька складених дій
    segments = split_compound_commands(orig)
    if len(segments) <= 1:
        return _match_single_action(orig, norm, custom_apps, custom_synonyms)

    intents: list[CommandIntent] = []
    prev_target = "active"
    for seg in segments:
        seg_norm = normalize_command_text(seg)
        intent = _match_single_action(
            seg,
            seg_norm,
            custom_apps=custom_apps,
            custom_synonyms=custom_synonyms,
            previous_target=prev_target,
        )
        if intent is None:
            # Якщо хоча б один сегмент не впізнано детерміновано — віддаємо весь запит у Smart path LLM
            return None

        # Оновлюємо ціль для наступних кроків у ланцюжку (наприклад: відкрили браузер -> перенесли його)
        if intent.skill in ("app.launch", "file.open", "file.create", "web.search"):
            prev_target = str(intent.args.get("name") or intent.args.get("target") or "active")

        intents.append(intent)

    # Якщо всі кроки ланцюжка успішно розпізнані
    if not intents:
        return None

    first = intents[0]
    return CommandIntent(
        skill=first.skill,
        args=first.args,
        confidence=min(i.confidence for i in intents),
        needs_confirm=any(i.needs_confirm for i in intents),
        steps=intents,
    )
