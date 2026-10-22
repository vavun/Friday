# CHANGELOG — FRIDAY

## [1.0.0] — Етапи 5–9: Введення тексту, Вебпошук, Помилка → Документація, Telegram, Antigravity CLI та Безпека/Профілювання (2026-10-22)

### Додано
- **Етап 5 — Введення тексту й вебпошук**:
  - `friday.skills.typing_text` (`TextTyper`, `TextTypeSkill`, `PyperclipClipboardBackend`): швидка й надійна вставка кириличного та технічного тексту через буфер обміну + `Ctrl+V` із гарантованим збереженням і відновленням попереднього вмісту буфера обміну (`finally`).
  - `friday.skills.web` (`WebBrowserService`, `WebSearchSkill`, `build_google_search_url`, `resolve_site_domain`): побудова URL пошуку Google з підтримкою `site:<домен>` (`python`, `fastapi`, `pydantic`, `mdn` тощо) та відкриття відомих сайтів документації.
- **Етап 6 — Помилка → документація**:
  - `friday.skills.docs_lookup` (`DocsLookupService`, `DocsLookupSkill`, `trim_traceback_text`, `parse_traceback_heuristic`): обрізання буфера обміну до першого рядка помилки + останніх ~40 рядків traceback, миттєва евристика без ШІ для визначення бібліотеки (`fastapi`, `pydantic`, `python`, `mdn`) й винятку (`TypeError`, `ValidationError`, `ModuleNotFoundError`), відкриття нового вікна браузера з очікуванням його HWND, переміщення на монітор `docs` та **повернення фокусу в активне вікно редактора** (без крадіжки фокусу).
  - Підключено гарячу клавішу `explain_clipboard` (`ctrl+alt+d`) у `friday.app.FridayApp`.
- **Етап 7 — Telegram**:
  - `friday.skills.telegram` (`TelegramService`, `TelegramSendSkill`, `resolve_contact_fuzzy`, `format_telegram_confirm_prompt`): лінива інтеграція з `telethon` (локальна сесія `friday_telegram.session` у `.gitignore`), кешування діалогів, нечіткий пошук контакту з морфологічною нормалізацією українських відмінків імен («Олегу» → «Олег Коваленко», «Марині» → «Марина Dev») та обов'язковий Confirm Gate (`Олегу: "…" — відправити? (Enter / Esc)`).
- **Етап 8 — Інтеграція Antigravity CLI**:
  - `friday.skills.agent_cli` (`AgentCliService`, `AgentRunSkill`, `build_agent_cli_command`, `resolve_project_workdir`, `resolve_agent_executable`): перевірено реальні прапорці `agy --help` (`-i` / `--prompt-interactive`), автоматичне визначення робочої папки проєкту за заголовком вікна редактора або зі списку `[paths].allowed_folders`, жорстка заборона `--dangerously-skip-permissions` / `--yes` / `auto_approve` та обов'язкове ручне підтвердження перед запуском.
- **Етап 9 — Безпека, надійність, оптимізація**:
  - Розширено `friday.safety` (`validate_intent_safety`, `should_require_confirmation`, `build_confirmation_prompt`) для централізованого блокування деструктивних патернів перед виконанням будь-якої навички.
  - Реалізовано `friday.autostart` (`enable_autostart`, `disable_autostart`, `is_autostart_enabled`, `build_autostart_script`) для фонового автозапуску через `pythonw.exe` без консольного вікна.
  - Додано зведену таблицю профілювання затримок `TimingTracker.format_summary_table()` у `friday.util.timing` та ізоляцію винятків навичок у головному циклі `FridayApp`.
  - Додано комплексні тести `tests/test_stages5_to_9.py`.

## [0.9.0] — Етап 4: Роутер (Fast path rules + Smart path LLM JSON) (2026-10-22)

### Додано
- Реалізовано `friday.router.rules` (`match_fast_rule`, `normalize_command_text`, `resolve_target_name`, `TARGET_SYNONYMS`, `MONITOR_WORDS`):
  - Детермінований Fast path для україномовних голосових команд без виклику ШІ: `window.move` (переміщення між моніторами 1/2/3 та снап `left`/`right`/`center`), `window.resize` (`bigger`/`smaller`/`maximize`/`restore`), `window.focus`, `app.launch`, `gesture.drag` (`start`/`stop`), `web.search` (із визначенням доменів `python`/`fastapi`/`pydantic`/`mdn`), `docs.lookup`, `text.type`, `telegram.send` (`needs_confirm=True`), `agent.run` (`needs_confirm=True`).
  - Нормалізація тексту (нижній регістр, апострофи, кінцева пунктуація, типові українські відмінкові закінчення).
- Реалізовано `friday.router.llm_parser` (`LlmCommandParser`, `extract_json_text`, `SYSTEM_PROMPT_TEMPLATE`):
  - Smart path через Gemini Flash (`gemini-3.5-flash-lite`, `temperature=0.0`, `max_output_tokens=200`, `response_mime_type="application/json"`, таймаут `5.0 с`).
  - Валідація виводу за схемою `CommandIntent` із автоматичним 1 ретраєм при невалідному JSON/схемі та перетворенням на `clarify` при `confidence < 0.7` або спробі запуску програми поза білим списком `[apps]`.
- Реалізовано `friday.router.CommandRouter` та інтегровано повний цикл маршрутизації й Confirm Gate у `friday.app.FridayApp` (`TRANSCRIBING -> ROUTING -> (CONFIRMING: Enter/Esc) -> EXECUTING -> IDLE`).
- Додано набір тестів `tests/test_router.py` (корпус із 35 україномовних фраз: `85.7%` через Fast path, середня затримка `0.56 мс`, максимальна `10.50 мс` при цілі `< 150 мс`).

## [0.8.0] — Лаунчер (Під-етап L3): Сторінки Folders (`path_rules.py`) та Apps (`app_scanner.py`) (2026-10-22)

### Додано
- Реалізовано `friday.launcher.services.path_rules` (`validate_folder_path`, `check_folder_status`, `PathValidationResult`):
  - Нормалізація шляху (`expandvars` + `expanduser` + `resolve`).
  - Жорстка заборона кореня диска (`C:\`), системних каталогів (`Windows`, `Program Files`, `Program Files (x86)`, `ProgramData`, `System Volume Information`, `$Recycle.Bin`), усієї домашньої директорії користувача та кореня `AppData`.
  - Попередження (`warning`) при виборі дуже широких папок профілю (`Downloads`, `Documents`, `Desktop`, `OneDrive`) або папок першого рівня від кореня диска.
- Реалізовано `friday.launcher.pages.folders.FoldersPage`:
  - Додавання папки через діалог вибору (`QFileDialog.getExistingDirectory`), задання мітки та режиму доступу (`read_only` / `read_write`).
  - Живий список дозволених папок із перевіркою статусу (`● Існує` / `▲ Недоступна`) та кнопкою `Видалити`, збереження у `[paths].allowed_folders` (`config.local.toml`).
- Реалізовано `friday.launcher.services.app_scanner` (`parse_lnk_bytes`, `parse_lnk_file`, `scan_start_menu_apps`, `validate_app_executable`, `build_stub_lnk_bytes`):
  - Автономний бінарний парсер ярликів Windows `.lnk` (`MS-SHLLINK` header `0x0000004C`) для сканування меню Пуск із автоматичним відсіканням деінсталяторів (`uninstall`, `unins000`) та перевіркою розширення виконуваного файлу (`.exe`, `.lnk`, `.bat`, `.cmd`, `.com`).
- Реалізовано `friday.launcher.pages.apps.AppsPage` та розширено `friday.skills.apps.AppLauncher`:
  - Ручне додавання програм із необов'язковими аргументами та голосовими синонімами через кому (`[apps]` + `[app_synonyms]` у `config.local.toml`).
  - Фоновий сканер меню Пуск (`StartMenuScanWorker`) із чекбоксами для пакетного імпорту вибраних програм.
- Додано юніт- та GUI-тести `tests/test_launcher_stage3.py`.

## [0.7.0] — Лаунчер (Під-етап L2): Сторінка Keys (.env), Dashboard Preflight та керування процесом ядра (2026-10-22)

### Додано
- Реалізовано `friday.launcher.services.env_store` (`EnvStore`, `mask_secret`, `ensure_env_in_gitignore`):
  - Атомарне оновлення ключів (`GEMINI_API_KEY`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) у `.env` без зміни коментарів, порожніх рядків та сторонніх змінних.
  - Автоматична перевірка наявності `.env` у `.gitignore` перед першим записом із попередженням користувача, якщо `.env` довелося додати.
  - Маскування секретів після збереження: відображаються лише останні 4 символи (`••••••••abcd`).
- Реалізовано `friday.launcher.services.key_check` (`verify_gemini_api_key`, `KeyCheckResult`):
  - Мінімальний тестовий запит через актуальний SDK `google-genai` (`gemini-3.5-flash-lite`, `max_output_tokens=4`) в окремому потоці `KeyCheckWorker` (`QThread`), без блокування GUI.
  - Зрозуміла обробка помилок авторизації (`401/403`) та відсутності мережі (`offline`).
- Реалізовано `friday.launcher.services.process_manager` (`CoreProcessManager`, `CoreProcessStatus`, `sanitize_log_line`):
  - Запуск ядра `python -m friday` як дочірнього процесу без консольного вікна (`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`).
  - Автоматичне маскування секретних ключів у потоці `stdout/stderr`, захист від подвійного запуску, коректна зупинка (`terminate` -> `kill` за таймаутом), виявлення аварійного завершення (`rc != 0` -> статус `ERROR`) та перезапуск (`restart`).
- Реалізовано `friday.launcher.services.preflight` (`run_preflight_checks`) та повні сторінки `DashboardPage` і `KeysPage`:
  - Індикація статусу ядра (`stopped` / `starting` / `running` / `error`), кнопки `ЗАПУСТИТИ FRIDAY`, `ЗУПИНИТИ`, `ПЕРЕЗАПУСТИТИ`, синхронізовані з меню системного трею.
  - Зведення конфігурації (активні гарячі клавіші, STT-бекенд, кількість дозволених папок і програм) та Preflight-діагностика (ключ Gemini, мікрофон через WinMM, залежності, білий список програм) із кнопками швидкого переходу `Виправити`.
- Додано юніт- та GUI-тести `tests/test_launcher_stage2.py`.

## [0.6.0] — Лаунчер (Під-етап L1): Каркас, неонова кіберпанк-тема, навігація та `config.local.toml` (2026-10-22)

### Додано
- Створено ізольований пакет десктопного застосунку `friday.launcher` (`python -m friday.launcher` та `run_launcher.bat`), який не імпортує важкі модулі ядра (STT, аудіо, MediaPipe).
- Додано локальні шрифти з відкритою ліцензією OFL-1.1 у `src/friday/launcher/assets/fonts/` (`Rajdhani-Bold`, `Rajdhani-SemiBold` для техно-заголовків та `JetBrainsMono-Regular` для логів).
- Реалізовано `friday.launcher.theme`: неонова кіберпанк-палітра (`#05080F`, `#0A0F1C`, `#00E5FF`, `#2979FF`), QSS-стилізація, ефект світіння `QGraphicsDropShadowEffect` та генератор векторної іконки.
- Реалізовано `friday.launcher.pages.NeonOrbWidget` (пульсуюча орба `<= 24 FPS`, яка автоматично вимикає таймер у прихованому стані / треї -> `~0% CPU`) та каркас усіх 9 сторінок (`dashboard`, `keys`, `folders`, `apps`, `voice`, `monitors`, `safety`, `logs`, `settings`) із лівим меню навігації.
- Додано підтримку `config.local.toml` (додано у `.gitignore`) з рекурсивним накладанням поверх `config.toml` у `friday.config.load_config` та атомарним записом із валідацією через `ConfigStore` (`friday.launcher.services.config_store`).
- Додано загальний перевіряльник дозволених шляхів `friday.safety.check_path_allowed` для `[paths].allowed_folders`.
- Додано юніт- та GUI smoke-тести `tests/test_launcher_stage1.py`.

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
