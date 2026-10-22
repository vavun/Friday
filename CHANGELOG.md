# CHANGELOG — FRIDAY

## [0.2.0] — Етап 1: Гаряча клавіша, запис звуку та зворотний зв'язок (2026-10-22)

### Додано
- Реалізовано `friday.audio` (`AudioRecorder` на базі `sounddevice` + `numpy`, 16 кГц mono `int16`, жорсткий ліміт `max_seconds = 15`, відсікання кліків `< 300 мс`, м'яка нормалізація гучності та збереження запису у `temp/latest.wav` для перевірки).
- Реалізовано `friday.hotkeys` (`HotkeyListener` на базі `pynput.keyboard.Listener` в окремому потоці з передачею подій у `asyncio.Queue` через `loop.call_soon_threadsafe` без polling у циклі; підтримка `hold-to-talk`, `ctrl+alt+d`, `esc`, `enter`).
- Реалізовано `friday.feedback` (`FeedbackManager`: звукові сигнали початку/кінця запису та помилки через `winsound.Beep` і Windows Toast-сповіщення через `winotify`).
- Інтегровано цикл `Hold-to-Talk` (`VOICE_PRESS` → старт запису, `VOICE_RELEASE` + `tail_ms` → стоп запису) у `friday.app.FridayApp`.
- Додано юніт-тести `tests/test_stage1.py`.

## [0.1.0] — Етап 0: Каркас (2026-10-22)

### Додано
- Створено повну структуру проєкту згідно з розділом 5 `fridayplan.md`.
- Налаштовано `pyproject.toml`, `config.toml`, `.env.example`, `.gitignore`.
- Реалізовано `friday.config` (валідація `config.toml` + `.env` через Pydantic v2, захист секретів у `repr`, заборона `auto_approve=true`, ротація логів у `logs/friday.log`).
- Реалізовано `friday.util.timing` (контекстний менеджер `measure_time` та `TimingTracker`).
- Реалізовано `friday.app` (автомат станів `AssistantState`, коректне завершення по `Ctrl+C`) та точку входу `friday.__main__` (`python -m friday`).
- Реалізовано базові контракти `friday.router.schema` (`CommandIntent`), `friday.stt.base` (`Transcriber`), `friday.skills.base` (`Skill`), а також базові правила безпеки `friday.safety` (перенесено та розширено патерни з проєкту `Jarvis`).
- Додано юніт-тести `tests/test_stage0.py`.
