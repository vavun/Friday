# CHANGELOG — FRIDAY

## [0.1.0] — Етап 0: Каркас (2026-10-22)

### Додано
- Створено повну структуру проєкту згідно з розділом 5 `fridayplan.md`.
- Налаштовано `pyproject.toml`, `config.toml`, `.env.example`, `.gitignore`.
- Реалізовано `friday.config` (валідація `config.toml` + `.env` через Pydantic v2, захист секретів у `repr`, заборона `auto_approve=true`, ротація логів у `logs/friday.log`).
- Реалізовано `friday.util.timing` (контекстний менеджер `measure_time` та `TimingTracker`).
- Реалізовано `friday.app` (автомат станів `AssistantState`, коректне завершення по `Ctrl+C`) та точку входу `friday.__main__` (`python -m friday`).
- Реалізовано базові контракти `friday.router.schema` (`CommandIntent`), `friday.stt.base` (`Transcriber`), `friday.skills.base` (`Skill`), а також базові правила безпеки `friday.safety` (перенесено та розширено патерни з проєкту `Jarvis`).
- Додано юніт-тести `tests/test_stage0.py`.
