# CHANGELOG — FRIDAY

## [0.5.0] — Етап A: Навичка `gesture.drag` (відстеження руки через камеру) (2026-10-22)

### Додано
- Створено пакет `friday.gesture` для керування вікнами жестами руки на одній камері:
  - `camera.py` (`CameraStream`): захоплення `640x480` при `15–20 FPS` через DirectShow (`cv2.CAP_DSHOW`), миттєве звільнення камери (`~277 мс < 1 с`), жодного запису кадрів на диск.
  - `hand_tracker.py` (`HandTracker`): інтеграція з актуальним для Python 3.12 API `mediapipe.tasks.python.vision.HandLandmarker` (`RunningMode.VIDEO`, `num_hands=1`), оптимізація вхідного тензора (`320x240` stride-downscale) для затримки `< 100 мс`.
  - `gestures.py` (`PinchDetector`, `GestureStateMachine`, `compute_pinch_ratio`, `map_normalized_to_virtual_desktop`, `find_monitor_at_point`, `find_neighbor_monitor`): жест «щипок» як відношення відстані `(4..8) / (0..9)` з подвійним порогом гістерезису (`pinch_on` / `pinch_off`), утриманням `≥ 150 мс`, автоматом станів `IDLE -> HOVER -> DRAGGING -> DROP` та відображенням координат на віртуальний робочий стіл усіх 3 моніторів.
  - `smoothing.py` (`OneEuroFilter1D`, `OneEuroFilter2D`): адаптивне згладжування координат руки фільтром One Euro.
  - `controller.py` (`GestureWindowMover`, `GestureRuntimeController`, `GestureDragSkill`): режими `drag` (обмеження `SetWindowPos` до `~30 Гц` + прилипання до монітора після відпускання) та `swipe` (щипок + різкий рух убік `< 1 с`), авто-вимкнення камери після `idle_timeout_s`, скасування перетягування по `Esc` з поверненням вікна на початкову позицію.
- Розширено `friday.skills.windows.WindowManager.validate_draggable_window`: блокування системних вікон робочого столу (`Progman`, `WorkerW`), панелі завдань (`Shell_TrayWnd`, `Shell_SecondaryTrayWnd`) та процесів з правами Адміністратора (UIPI).
- Додано секцію `[gesture]` у `config.toml` та `GestureConfig` у `friday.config`.
- Додано юніт-тести `tests/test_gesture_stage_a.py` (8 тестів на синтетичних наборах точок руки без камери).

## [0.4.0] — Етап 3: Менеджер вікон і моніторів (Fast Path) (2026-10-22)

### Додано
- Реалізовано `friday.util.monitors` (`enable_dpi_awareness`, `list_monitors` через `EnumDisplayMonitors` + `GetMonitorInfo` із вибором `Work` area замість повного екрана).
- Реалізовано `friday.skills.windows` (`WindowManager`, `RealWin32Backend`, `WindowMoveSkill`, `WindowResizeSkill`, `WindowFocusSkill`):
  - Пошук вікон за процесом і заголовком з кешуванням HWND та перевіркою `IsWindow`.
  - Коректне переміщення між моніторами (`SW_RESTORE` → `SetWindowPos` → `SW_MAXIMIZE`).
  - Снап (`left`/`right`/`center`) з компенсацією невидимих рамок Windows 10/11 через `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`.
  - Зміна розміру (`bigger`/`smaller` з кроком 12% від `work_area`).
  - Надійний фокус (`SetForegroundWindow` через `AttachThreadInput` + короткий Alt-трюк) та захист від падінь при UIPI (вікна з правами Адміністратора).
- Реалізовано `friday.skills.apps` (`AppLauncher`, `AppLaunchSkill`): запуск виключно з білого списку `[apps]` у `config.toml` без `shell=True`.
- Реалізовано `friday.skills.base.SkillRegistry` та утиліту командного рядка `python -m friday.dev` (`monitors`, `window move/snap/resize/focus`, `app launch`).
- Додано юніт-тести `tests/test_stage3.py`.

## [0.3.0] — Етап 2: Розпізнавання мовлення (STT) (2026-10-22)

### Додано
- Реалізовано `friday.stt.gemini.GeminiTranscriber` (на базі `Jarvis/Server/app/stt.py`): повторне використання одного екземпляра `genai.Client`, підтримка `AudioTranscriptionConfig` (`VERBATIM` / `SMART`) та спеціалізованого україномовного промпту зі збереженням англійських технічних термінів латиницею (`FastAPI`, `pydantic`, `traceback`, `git`, `TypeError`).
- Реалізовано `friday.stt.whisper_local.WhisperLocalTranscriber`: ліниве завантаження `faster-whisper` (`small`, `compute_type="int8"`) виключно тоді, коли обрано бекенд `whisper_local`, метод `warmup()` при старті та підказка `initial_prompt` для української мови й технічних термінів.
- Додано фабрику `friday.stt.create_transcriber` та інтегровано стан `TRANSCRIBING` у `FridayApp` із вимірюванням повної затримки `release_to_transcript` (від відпускання клавіші до отримання тексту).
- Додано юніт-тести `tests/test_stage2.py` (включно з набором із 10 тестових україномовних фраз із технічними термінами).

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
