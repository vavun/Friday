"""Динамічні навички FRIDAY: авто-пошук програм (app.discover), збереження пайплайнів (pipeline.save) та створення навичок (skill.create / skill.run)."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Callable

from friday.config import AppConfig
from friday.launcher.services.app_scanner import (
    ScannedApp,
    format_app_command,
    normalize_voice_name,
    scan_start_menu_apps,
)
from friday.launcher.services.config_store import ConfigStore
from friday.router.clef_client import (
    _safe_json_loads,
    normalize_skill_args,
    normalize_skill_name,
    normalize_step_dict,
)
from friday.safety import validate_intent_safety
from friday.skills.apps import AppLauncher
from friday.skills.base import SkillRegistry

log = logging.getLogger("friday.skills.dynamic")

# Словник популярних українських назв застосунків -> кандидати для пошуку у Пуск / Реєстрі / PATH
COMMON_APP_CANDIDATES: dict[str, list[str]] = {
    "блокнот": ["notepad", "notepad.exe", "notepad++"],
    "блокноті": ["notepad", "notepad.exe", "notepad++"],
    "notepad": ["notepad", "notepad.exe"],
    "калькулятор": ["calc", "calc.exe", "calculator"],
    "calc": ["calc", "calc.exe"],
    "провідник": ["explorer", "explorer.exe", "file explorer"],
    "експлорер": ["explorer", "explorer.exe", "file explorer"],
    "пейнт": ["mspaint", "mspaint.exe", "paint"],
    "paint": ["mspaint", "mspaint.exe", "paint"],
    "діскорд": ["discord", "discord.exe"],
    "дискорд": ["discord", "discord.exe"],
    "спотіфай": ["spotify", "spotify.exe"],
    "спотік": ["spotify", "spotify.exe"],
    "стім": ["steam", "steam.exe"],
    "стiм": ["steam", "steam.exe"],
    "обс": ["obs", "obs64", "obs studio", "obs64.exe"],
    "obs": ["obs", "obs64", "obs studio", "obs64.exe"],
    "блендер": ["blender", "blender.exe"],
    "фотошоп": ["photoshop", "adobe photoshop", "photoshop.exe"],
    "photoshop": ["photoshop", "adobe photoshop", "photoshop.exe"],
    "фігма": ["figma", "figma.exe"],
    "figma": ["figma", "figma.exe"],
    "постман": ["postman", "postman.exe"],
    "postman": ["postman", "postman.exe"],
    "докер": ["docker", "docker desktop", "docker desktop.exe"],
    "docker": ["docker", "docker desktop", "docker desktop.exe"],
    "слак": ["slack", "slack.exe"],
    "slack": ["slack", "slack.exe"],
    "зум": ["zoom", "zoom.exe"],
    "zoom": ["zoom", "zoom.exe"],
    "ворд": ["winword", "word", "winword.exe"],
    "word": ["winword", "word", "winword.exe"],
    "ексель": ["excel", "excel.exe"],
    "excel": ["excel", "excel.exe"],
    "паверпоінт": ["powerpnt", "powerpoint", "powerpnt.exe"],
    "влц": ["vlc", "vlc.exe"],
    "vlc": ["vlc", "vlc.exe"],
    "гіт баш": ["git bash", "git-bash.exe"],
    "термінал": ["wt", "wt.exe", "powershell", "powershell.exe", "cmd.exe"],
    "павершел": ["powershell", "powershell.exe", "pwsh.exe"],
    "диспетчер завдань": ["taskmgr", "taskmgr.exe"],
}

# Транслітерація кирилиці -> латиниця для нечіткого зіставлення голосових назв програм
_UKR_TO_LAT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ye", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "yi", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ь": "", "ю": "yu", "я": "ya",
}

_MAX_PIPELINE_RECURSION_DEPTH = 5


def transliterate_ukr_to_lat(text: str) -> str:
    """Транслітерує українську назву латиницею для пошуку відповідного .exe або ярлика."""
    return "".join(_UKR_TO_LAT.get(ch, ch) for ch in (text or "").lower())


class AppDiscoveryService:
    """Автоматично знаходить нові програми у Windows, зберігає їх у config.local.toml та реєструє на льоту."""

    def __init__(
        self,
        config: AppConfig,
        app_launcher: AppLauncher,
        config_store: ConfigStore | None = None,
        scanner_fn: Callable[[], list[ScannedApp]] | None = None,
        which_fn: Callable[[str], str | None] | None = None,
    ) -> None:
        self.config = config
        self.app_launcher = app_launcher
        self.config_store = config_store
        self._scanner_fn = scanner_fn or (
            lambda: scan_start_menu_apps(require_target_exists=True, include_registry=True)
        )
        self._which_fn = which_fn or shutil.which
        self._scanned_cache: list[ScannedApp] | None = None
        self._indexed_cache: list[tuple[ScannedApp, str, str]] | None = None

    def _get_scanned_apps(self, refresh: bool = False) -> list[ScannedApp]:
        if self._scanned_cache is None or refresh:
            try:
                self._scanned_cache = list(self._scanner_fn())
            except Exception as exc:
                log.warning("Помилка сканування меню Пуск/реєстру: %s", exc)
                self._scanned_cache = []
            self._indexed_cache = [
                (
                    app,
                    normalize_voice_name(Path(app.exe_path).stem),
                    normalize_voice_name(app.display_name),
                )
                for app in self._scanned_cache
            ]
        return self._scanned_cache

    def _get_indexed_apps(self, refresh: bool = False) -> list[tuple[ScannedApp, str, str]]:
        self._get_scanned_apps(refresh=refresh)
        return self._indexed_cache or []

    def find_executable(self, raw_query: str, extra_candidates: list[str] | None = None) -> tuple[str, str] | None:
        """Шукає програму за голосовою назвою або кандидатами; повертає (canonical_key, command_path) або None."""
        cleaned = normalize_voice_name(raw_query)
        if not cleaned or cleaned == "app":
            return None

        # 0. Якщо вже є у білому списку або синонімах
        try:
            existing_key = self.app_launcher.resolve_app_key(cleaned)
            return (existing_key, self.app_launcher.allowed_apps[existing_key])
        except PermissionError:
            pass

        candidates: list[str] = [cleaned]
        if extra_candidates:
            for c in extra_candidates:
                c_norm = normalize_voice_name(c)
                if c_norm and c_norm not in candidates:
                    candidates.append(c_norm)

        if cleaned in COMMON_APP_CANDIDATES:
            for alias in COMMON_APP_CANDIDATES[cleaned]:
                if alias.lower() not in candidates:
                    candidates.append(alias.lower())

        translit = transliterate_ukr_to_lat(cleaned)
        if translit and translit not in candidates:
            candidates.append(translit)

        # 1. Перевірка системного PATH / відомих системних утиліт через shutil.which
        for cand in candidates:
            if " " not in cand:
                hit = self._which_fn(cand) or self._which_fn(f"{cand}.exe")
                if hit and Path(hit).exists():
                    canon = normalize_voice_name(Path(hit).stem)
                    return (canon, str(Path(hit).resolve()))

        # 2. Пошук серед попередньо проіндексованих програм меню Пуск, Робочого столу та Реєстру
        indexed = self._get_indexed_apps()
        cand_norms = [normalize_voice_name(c) for c in candidates if normalize_voice_name(c)]

        # Точний збіг за voice_name, display_name або ім'ям exe
        for cand_clean in cand_norms:
            for app, exe_stem, disp_norm in indexed:
                if cand_clean in (app.voice_name, disp_norm, exe_stem):
                    cmd = format_app_command(app.exe_path, app.arguments)
                    return (app.voice_name, cmd)

        # Частковий збіг (містить слово довжиною >= 3 символи)
        for cand_clean in cand_norms:
            if len(cand_clean) < 3:
                continue
            for app, exe_stem, disp_norm in indexed:
                if (
                    cand_clean in app.voice_name
                    or cand_clean in disp_norm
                    or cand_clean in exe_stem
                ):
                    cmd = format_app_command(app.exe_path, app.arguments)
                    return (app.voice_name, cmd)

        return None

    def discover_and_register(
        self,
        raw_name: str,
        synonyms: list[str] | None = None,
        exe_candidates: list[str] | None = None,
        persist: bool = True,
    ) -> tuple[str, str] | None:
        """Знаходить програму, реєструє її в AppLauncher + AppConfig і зберігає у config.local.toml."""
        found = self.find_executable(raw_name, extra_candidates=exe_candidates)
        if found is None:
            return None

        canon_key, cmd_path = found
        canon_key = canon_key.lower().strip()

        # Оновлюємо оперативний стан у пам'яті
        self.config.apps[canon_key] = cmd_path
        self.app_launcher.allowed_apps[canon_key] = cmd_path

        all_synonyms: set[str] = set()
        raw_clean = (raw_name or "").strip().lower()
        if raw_clean and raw_clean != canon_key:
            all_synonyms.add(raw_clean)
        if synonyms:
            for s in synonyms:
                s_clean = str(s).strip().lower()
                if s_clean and s_clean != canon_key:
                    all_synonyms.add(s_clean)

        for syn in all_synonyms:
            self.config.app_synonyms[syn] = canon_key
            self.app_launcher.synonyms[syn] = canon_key

        # Зберігаємо у config.local.toml, якщо підключено ConfigStore
        if persist and self.config_store is not None:
            try:
                overrides: dict[str, Any] = {"apps": {canon_key: cmd_path}}
                if all_synonyms:
                    overrides["app_synonyms"] = {syn: canon_key for syn in all_synonyms}
                self.config_store.save_local_overrides(overrides)
                log.info(
                    "💾 Збережено нову програму у config.local.toml: [%s] -> %s (синоніми: %s)",
                    canon_key,
                    cmd_path,
                    sorted(all_synonyms),
                )
            except Exception as exc:
                log.warning("Не вдалося зберегти нову програму у config.local.toml: %s", exc)

        return (canon_key, cmd_path)


class AppDiscoverSkill:
    """Навичка app.discover: знаходить нову програму, зберігає її у навички/конфіг та одразу запускає."""

    name = "app.discover"

    def __init__(self, discovery: AppDiscoveryService, launcher: AppLauncher) -> None:
        self.discovery = discovery
        self.launcher = launcher

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        norm_args = normalize_skill_args("app.discover", args)
        raw_name = str(
            norm_args.get("name")
            or norm_args.get("query")
            or norm_args.get("app_name")
            or args.get("app_name")
            or ""
        ).strip()
        if not raw_name:
            raise ValueError("Не вказано назву програми для пошуку (args.name)")

        synonyms = norm_args.get("synonyms")
        syn_list = [str(x) for x in synonyms] if isinstance(synonyms, list) else []
        candidates = norm_args.get("candidates")
        cand_list = [str(x) for x in candidates] if isinstance(candidates, list) else []
        launch_after = bool(norm_args.get("launch", True))

        found = await asyncio.to_thread(
            self.discovery.discover_and_register,
            raw_name,
            syn_list,
            cand_list,
        )
        if found is None:
            raise PermissionError(f"Програму «{raw_name}» не знайдено на цьому комп'ютері.")

        canon_key, cmd_path = found
        if launch_after:
            res = await asyncio.to_thread(self.launcher.launch_sync, canon_key)
            res["discovered"] = True
            return res

        return {"app": canon_key, "command": cmd_path, "status": "registered", "discovered": True}


class DynamicSkillManager:
    """Керує збереженням та виконанням користувацьких пайплайнів ([pipelines]) і навичок ([custom_skills])."""

    def __init__(
        self,
        config: AppConfig,
        registry: SkillRegistry,
        config_store: ConfigStore | None = None,
        popen_fn: Callable[..., Any] | None = None,
        app_discovery: AppDiscoveryService | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.config_store = config_store
        self._popen_fn = popen_fn or subprocess.Popen
        self.app_discovery = app_discovery

    def save_pipeline(
        self,
        trigger: str,
        steps: list[dict[str, Any]],
        persist: bool = True,
    ) -> str:
        """Зберігає послідовність кроків (pipeline) за голосовим тригером у пам'ять та config.local.toml."""
        clean_trigger = (trigger or "").strip().lower()
        if not clean_trigger:
            raise ValueError("Не вказано назву або голосовий тригер пайплайну")
        if not steps:
            raise ValueError("Пайплайн має містити щонайменше один крок")

        normalized_steps: list[dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            norm_st = normalize_step_dict(step)
            sk = norm_st["skill"]
            st_args = norm_st["args"]
            if not sk or sk == "pipeline.save":
                continue
            if sk == "skill.run" and str(st_args.get("name", "")).strip().lower() == clean_trigger:
                raise ValueError(f"Пайплайн '{clean_trigger}' не може рекурсивно викликати сам себе")
            validate_intent_safety(sk, st_args)
            normalized_steps.append({"skill": sk, "args": st_args})

        if not normalized_steps:
            raise ValueError("Пайплайн не містить валідних кроків для збереження")

        self.config.pipelines[clean_trigger] = normalized_steps
        if persist and self.config_store is not None:
            try:
                self.config_store.save_local_overrides(
                    {"pipelines": {clean_trigger: normalized_steps}}
                )
                log.info(
                    "💾 Збережено пайплайн '%s' (%d кроків) у config.local.toml",
                    clean_trigger,
                    len(normalized_steps),
                )
            except Exception as exc:
                log.warning("Не вдалося зберегти пайплайн у config.local.toml: %s", exc)

        return clean_trigger

    def save_custom_skill(
        self,
        name: str,
        spec: dict[str, Any],
        persist: bool = True,
    ) -> str:
        """Зберігає нову динамічну навичку у [custom_skills] config.local.toml."""
        clean_name = (name or "").strip().lower()
        if not clean_name:
            raise ValueError("Не вказано назву навички")

        spec_copy = dict(spec)
        if isinstance(spec_copy.get("steps"), list):
            spec_copy["steps"] = [
                normalize_step_dict(st) for st in spec_copy["steps"] if isinstance(st, dict)
            ]

        validate_intent_safety("skill.create", spec_copy)
        self.config.custom_skills[clean_name] = spec_copy

        if persist and self.config_store is not None:
            try:
                self.config_store.save_local_overrides(
                    {"custom_skills": {clean_name: spec_copy}}
                )
                log.info("💾 Збережено нову навичку '%s' у config.local.toml", clean_name)
            except Exception as exc:
                log.warning("Не вдалося зберегти навичку у config.local.toml: %s", exc)

        return clean_name

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        _depth: int = 0,
    ) -> list[dict[str, Any]]:
        """Послідовно виконує список кроків через SkillRegistry із протягуванням контексту вікон."""
        if _depth > _MAX_PIPELINE_RECURSION_DEPTH:
            raise RuntimeError("Перевищено максимальну глибину вкладеності пайплайнів (можлива рекурсія)")

        results: list[dict[str, Any]] = []
        last_app_key: str | None = None
        last_raw_app_name: str | None = None

        for idx, step in enumerate(steps):
            norm_st = normalize_step_dict(step) if isinstance(step, dict) else {"skill": "", "args": {}}
            sk_name = norm_st["skill"]
            sk_args = dict(norm_st["args"])
            validate_intent_safety(sk_name, sk_args)

            # Якщо крок app.launch звертається до ще не зареєстрованої програми, але є сервіс авто-пошуку — знаходимо її
            if sk_name == "app.launch" and self.app_discovery is not None:
                app_name = str(sk_args.get("name", "")).strip()
                if app_name:
                    try:
                        self.app_discovery.app_launcher.resolve_app_key(app_name)
                    except PermissionError:
                        disc_skill = self.registry.get("app.discover")
                        if disc_skill is not None:
                            sk_name = "app.discover"
                            sk_args = {"name": app_name, "launch": True}

            # Якщо попередній крок відкрив програму через app.discover/app.launch, оновлюємо target для window.*
            if sk_name in ("window.move", "window.resize", "window.focus") and last_app_key:
                cur_t = str(sk_args.get("target", "active")).strip().lower()
                if cur_t in ("його", "її", "це", "це вікно", "") or (
                    last_raw_app_name and cur_t == last_raw_app_name
                ):
                    sk_args["target"] = last_app_key
                elif cur_t in self.config.app_synonyms:
                    sk_args["target"] = self.config.app_synonyms[cur_t]

            skill = self.registry.get(sk_name)
            if skill is None:
                raise KeyError(f"Навичку '{sk_name}' не знайдено у реєстрі")

            if sk_name in ("app.launch", "app.discover"):
                last_raw_app_name = str(sk_args.get("name", "")).strip().lower()

            res = await skill.execute(sk_args)
            if isinstance(res, dict) and res.get("app"):
                last_app_key = str(res["app"]).strip().lower()
            elif sk_name == "web.search":
                last_app_key = "browser"

            results.append(res)
            if len(steps) > idx + 1:
                next_raw = steps[idx + 1]
                next_sk = normalize_skill_name(str(next_raw.get("skill", ""))) if isinstance(next_raw, dict) else ""
                if (
                    sk_name in ("app.launch", "app.discover", "file.open", "file.create", "web.search")
                    and next_sk in ("window.move", "window.resize", "window.focus")
                ):
                    await asyncio.sleep(0.5)
        return results

    async def execute_custom_spec(
        self,
        name: str,
        spec: dict[str, Any],
        _depth: int = 0,
    ) -> dict[str, Any]:
        """Виконує збережену користувацьку навичку за її специфікацією (steps / url / command)."""
        if _depth > _MAX_PIPELINE_RECURSION_DEPTH:
            raise RuntimeError(f"Перевищено глибину вкладеності для навички '{name}'")

        validate_intent_safety("skill.run", spec)
        action_type = str(spec.get("action_type", "command")).strip().lower()

        if action_type == "steps" or isinstance(spec.get("steps"), list):
            steps = list(spec.get("steps") or [])
            step_results = await self.execute_steps(steps, _depth=_depth + 1)
            return {"skill": name, "action_type": "steps", "results": step_results, "status": "executed"}

        if action_type == "url":
            url = str(spec.get("url") or spec.get("command") or "").strip()
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            await asyncio.to_thread(webbrowser.open, url)
            return {"skill": name, "action_type": "url", "url": url, "status": "executed"}

        # action_type == "command": безпечний запуск без shell=True
        raw_cmd = os.path.expandvars(str(spec.get("command", "")).strip())
        if not raw_cmd:
            raise ValueError(f"Навичка '{name}' не містить поля command або steps")

        parts = shlex.split(raw_cmd, posix=False)
        exe = parts[0].strip("\"'")
        resolved = shutil.which(exe) or exe
        cmd_args = [resolved, *[p.strip("\"'") for p in parts[1:]]]
        await asyncio.to_thread(self._popen_fn, cmd_args, shell=False)
        log.info("⚡ Виконано користувацьку навичку '%s': %s", name, cmd_args)
        return {"skill": name, "action_type": "command", "command": cmd_args, "status": "executed"}


class PipelineSaveSkill:
    """Навичка pipeline.save: зберігає новий пайплайн у навички та (опційно) одразу виконує його."""

    name = "pipeline.save"

    def __init__(self, manager: DynamicSkillManager) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        norm_args = normalize_skill_args("pipeline.save", args)
        trigger = str(norm_args.get("trigger") or norm_args.get("name") or "").strip()
        raw_steps = norm_args.get("steps")
        if isinstance(raw_steps, str):
            raw_steps = _safe_json_loads(raw_steps)
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("Для збереження пайплайну необхідний непорожній список steps")

        saved_key = self.manager.save_pipeline(trigger, raw_steps)
        run_now = bool(norm_args.get("run_now", True))
        executed_results: list[dict[str, Any]] = []
        if run_now:
            executed_results = await self.manager.execute_steps(self.manager.config.pipelines[saved_key])

        return {
            "pipeline": saved_key,
            "steps_count": len(self.manager.config.pipelines[saved_key]),
            "executed": run_now,
            "results": executed_results,
            "status": "saved",
        }


class SkillCreateSkill:
    """Навичка skill.create: створює нову функцію/навичку, зберігає у config.local.toml та виконує."""

    name = "skill.create"

    def __init__(self, manager: DynamicSkillManager) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        norm_args = normalize_skill_args("skill.create", args)
        name = str(norm_args.get("name") or norm_args.get("trigger") or "").strip().lower()
        trigger = str(norm_args.get("trigger") or name).strip().lower()
        action_type = str(norm_args.get("action_type", "command")).strip().lower()
        spec: dict[str, Any] = {
            "trigger": trigger,
            "action_type": action_type,
            "description": str(norm_args.get("description", "")).strip(),
        }
        if "command" in norm_args and norm_args["command"] is not None:
            spec["command"] = str(norm_args["command"]).strip()
        if "url" in norm_args and norm_args["url"] is not None:
            spec["url"] = str(norm_args["url"]).strip()
        if isinstance(norm_args.get("steps"), list):
            spec["steps"] = norm_args["steps"]

        saved_name = self.manager.save_custom_skill(name, spec)
        run_now = bool(norm_args.get("run_now", True))
        exec_res: dict[str, Any] | None = None
        if run_now:
            exec_res = await self.manager.execute_custom_spec(saved_name, self.manager.config.custom_skills[saved_name])

        return {
            "skill": saved_name,
            "trigger": trigger,
            "executed": run_now,
            "result": exec_res,
            "status": "created",
        }


class SkillRunSkill:
    """Навичка skill.run: запускає збережену користувацьку навичку або збережений пайплайн."""

    name = "skill.run"

    def __init__(self, manager: DynamicSkillManager) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        norm_args = normalize_skill_args("skill.run", args)
        target = str(norm_args.get("name") or norm_args.get("trigger") or "").strip().lower()
        if not target:
            raise ValueError("Не вказано назву навички або пайплайну для запуску")

        # 1. Перевірка у збережених пайплайнах (точний або нормалізований збіг)
        if target in self.manager.config.pipelines:
            steps = self.manager.config.pipelines[target]
            results = await self.manager.execute_steps(steps)
            return {"pipeline": target, "results": results, "status": "executed"}

        for pipe_key, steps in self.manager.config.pipelines.items():
            if pipe_key.strip().lower() == target:
                results = await self.manager.execute_steps(steps)
                return {"pipeline": pipe_key, "results": results, "status": "executed"}

        # 2. Перевірка у збережених користувацьких навичках (за ключем або тригером)
        if target in self.manager.config.custom_skills:
            spec = self.manager.config.custom_skills[target]
            return await self.manager.execute_custom_spec(target, spec)

        for sk_name, spec in self.manager.config.custom_skills.items():
            if str(spec.get("trigger", "")).strip().lower() == target or sk_name.strip().lower() == target:
                return await self.manager.execute_custom_spec(sk_name, spec)

        raise KeyError(f"Збережену навичку або пайплайн '{target}' не знайдено")
