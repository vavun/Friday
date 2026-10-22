"""Автомат станів та головна оркестрація асистента FRIDAY."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import StrEnum
from typing import Callable

from friday.audio import AudioRecorder
from friday.config import AppConfig
from friday.feedback import FeedbackManager
from friday.gesture.controller import GestureDragSkill, GestureRuntimeController
from friday.hotkeys import HotkeyEvent, HotkeyEventType, HotkeyListener
from friday.router import CommandIntent, CommandRouter
from friday.safety import (
    build_confirmation_prompt,
    should_require_confirmation,
    validate_intent_safety,
)
from friday.skills.agent_cli import AgentCliService, AgentRunSkill
from friday.skills.apps import AppLauncher, AppLaunchSkill
from friday.skills.base import SkillRegistry
from friday.skills.docs_lookup import DocsLookupService, DocsLookupSkill
from friday.skills.files import FileCreateSkill, FileOpenSkill, FileSystemService
from friday.skills.telegram import TelegramSendSkill, TelegramService
from friday.skills.typing_text import TextTypeSkill
from friday.skills.web import WebBrowserService, WebSearchSkill
from friday.skills.windows import (
    WindowFocusSkill,
    WindowManager,
    WindowMoveSkill,
    WindowResizeSkill,
)
from friday.stt import GeminiTranscriber, Transcriber, WhisperLocalTranscriber, create_transcriber

log = logging.getLogger("friday.app")


class AssistantState(StrEnum):
    """Стани автомата FRIDAY."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    ROUTING = "ROUTING"
    CONFIRMING = "CONFIRMING"
    EXECUTING = "EXECUTING"


class FridayApp:
    """Головний клас асистента, що керує станами, хоткеями, записом звуку та STT."""

    def __init__(
        self,
        config: AppConfig,
        recorder: AudioRecorder | None = None,
        feedback: FeedbackManager | None = None,
        transcriber: Transcriber | None = None,
        router: CommandRouter | None = None,
        registry: SkillRegistry | None = None,
        gesture_controller: GestureRuntimeController | None = None,
        listener_factory: Callable[..., HotkeyListener] | None = None,
    ) -> None:
        self.config = config
        self._state: AssistantState = AssistantState.IDLE
        self._stop_event = asyncio.Event()
        self.event_queue: asyncio.Queue[HotkeyEvent] = asyncio.Queue(maxsize=32)

        self.recorder = recorder or AudioRecorder(config=config.audio)
        self.feedback = feedback or FeedbackManager()
        self.transcriber: Transcriber = transcriber or create_transcriber(config)
        self.router: CommandRouter = router or CommandRouter(config=config)
        self.gesture_controller = gesture_controller
        self.registry: SkillRegistry = registry or self._build_default_registry()
        self._listener_factory = listener_factory
        self._hotkey_listener: HotkeyListener | None = None
        self._last_audio: bytes | None = None
        self._last_transcript: str = ""
        self._last_intent: CommandIntent | None = None
        self._pending_intent: CommandIntent | None = None

    def _build_default_registry(self) -> SkillRegistry:
        """Реєструє всі навички ядра FRIDAY (Етапи 3–8)."""
        reg = SkillRegistry()
        wm = WindowManager()
        reg.register(WindowMoveSkill(wm))
        reg.register(WindowResizeSkill(wm))
        reg.register(WindowFocusSkill(wm))
        app_launcher = AppLauncher(
            self.config.apps,
            custom_synonyms=self.config.app_synonyms,
        )
        reg.register(AppLaunchSkill(app_launcher))
        file_service = FileSystemService(
            allowed_folders=self.config.paths.allowed_folders,
            app_launcher=app_launcher,
        )
        reg.register(FileCreateSkill(file_service))
        reg.register(FileOpenSkill(file_service))
        reg.register(TextTypeSkill())
        reg.register(WebSearchSkill(WebBrowserService(docs_sites=self.config.docs_sites)))
        reg.register(
            DocsLookupSkill(
                DocsLookupService(
                    window_manager=wm,
                    docs_sites=self.config.docs_sites,
                    docs_monitor_0based=self.config.monitors.docs,
                )
            )
        )
        reg.register(
            TelegramSendSkill(
                TelegramService(
                    api_id=self.config.secrets.telegram_api_id,
                    api_hash=self.config.secrets.telegram_api_hash,
                )
            )
        )
        reg.register(
            AgentRunSkill(
                AgentCliService(
                    agent_config=self.config.agent,
                    allowed_folders=self.config.paths.allowed_folders,
                    window_manager=wm,
                )
            )
        )
        if self.gesture_controller is not None:
            reg.register(GestureDragSkill(self.gesture_controller))
        return reg

    @property
    def state(self) -> AssistantState:
        """Поточний стан асистента."""
        return self._state

    @property
    def last_audio(self) -> bytes | None:
        """Останній записаний WAV-буфер."""
        return self._last_audio

    @property
    def last_transcript(self) -> str:
        """Останній розпізнаний текст команди."""
        return self._last_transcript

    @property
    def last_intent(self) -> CommandIntent | None:
        """Остання маршрутизована команда."""
        return self._last_intent

    def transition(self, new_state: AssistantState) -> None:
        """Переводить автомат у новий стан із логуванням."""
        if self._state != new_state:
            log.info("Стан: %s -> %s", self._state.value, new_state.value)
            self._state = new_state

    def stop(self) -> None:
        """Сигналізує про завершення роботи головного циклу."""
        self._stop_event.set()

    async def handle_event(self, event: HotkeyEvent) -> None:
        """Обробляє одну подію гарячої клавіші згідно з автоматом станів."""
        if event.type == HotkeyEventType.VOICE_PRESS:
            await self._on_voice_press(event)
        elif event.type == HotkeyEventType.VOICE_RELEASE:
            await self._on_voice_release(event)
        elif event.type == HotkeyEventType.CONFIRM:
            await self._on_confirm()
        elif event.type == HotkeyEventType.CANCEL:
            await self._on_cancel()
        elif event.type == HotkeyEventType.GESTURE_PRESS:
            await self._on_gesture_press()
        elif event.type == HotkeyEventType.GESTURE_RELEASE:
            await self._on_gesture_release()
        elif event.type == HotkeyEventType.EXPLAIN_CLIPBOARD:
            await self._on_explain_clipboard()

    async def _on_explain_clipboard(self) -> None:
        """Миттєво відкриває документацію по помилці з буфера обміну на моніторі docs (Етап 6)."""
        intent = CommandIntent(
            skill="docs.lookup",
            args={"source": "clipboard"},
            confidence=1.0,
            needs_confirm=False,
        )
        self._last_intent = intent
        await self._execute_intent(intent)

    async def _on_voice_press(self, event: HotkeyEvent) -> None:
        """Старт запису по утриманню клавіші (без звукового сигналу)."""
        if self._state == AssistantState.LISTENING:
            return
        self.transition(AssistantState.LISTENING)
        started = self.recorder.start()
        latency_ms = (time.perf_counter() - event.timestamp) * 1000.0
        log.info("⏱ Затримка [hotkey_to_record_start]: %.2f мс", latency_ms)
        if not started:
            self.transition(AssistantState.IDLE)
            await self.feedback.notify(
                "Помилка мікрофона",
                "Не вдалося розпочати запис із мікрофона. Перевірте аудіопристрій.",
            )

    async def _on_voice_release(self, event: HotkeyEvent) -> None:
        """Дозапис tail_ms після відпускання клавіші, зупинка запису, STT, роутинг та виконання."""
        if self._state != AssistantState.LISTENING:
            return

        release_t0 = event.timestamp
        tail_s = self.config.audio.tail_ms / 1000.0
        if tail_s > 0:
            await asyncio.sleep(tail_s)

        wav_bytes = await asyncio.to_thread(self.recorder.stop)

        if not wav_bytes:
            self.transition(AssistantState.IDLE)
            return

        self._last_audio = wav_bytes
        if self.recorder.last_saved_path:
            log.info("Аудіо збережено у: %s", self.recorder.last_saved_path)

        self.transition(AssistantState.TRANSCRIBING)
        try:
            transcript = await self.transcriber.transcribe(wav_bytes)
        except Exception as exc:
            log.exception("Помилка розпізнавання мовлення (STT): %s", exc)
            self.transition(AssistantState.IDLE)
            await self.feedback.notify("Помилка STT", str(exc))
            return

        release_to_text_ms = (time.perf_counter() - release_t0) * 1000.0
        log.info("⏱ Затримка [release_to_transcript]: %.2f мс", release_to_text_ms)

        self._last_transcript = transcript.strip()
        if not self._last_transcript:
            log.info("Транскрипт порожній — повернення у IDLE.")
            self.transition(AssistantState.IDLE)
            return

        await self._route_and_dispatch(self._last_transcript)

    async def _route_and_dispatch(self, text: str) -> None:
        """Маршрутизує розпізнаний текст і передає команду на виконання або підтвердження."""
        self.transition(AssistantState.ROUTING)
        intent = await self.router.route(text)
        self._last_intent = intent

        if intent.skill == "clarify":
            question = str(intent.args.get("question", "Уточніть команду, будь ласка."))
            log.info("❓ Потрібне уточнення: %s", question)
            self.transition(AssistantState.IDLE)
            await self.feedback.notify("Уточніть команду", question)
            return

        # Перевірка навичок, що вимагають підтвердження (як одиночних, так і у складі ланцюжка)
        all_steps = intent.steps if intent.steps else [intent]
        needs_confirm_step = next(
            (
                s
                for s in all_steps
                if should_require_confirmation(s.skill, s.needs_confirm, self.config.safety)
            ),
            None,
        )
        if needs_confirm_step is not None:
            self._pending_intent = intent
            self.transition(AssistantState.CONFIRMING)
            prompt_msg = build_confirmation_prompt(needs_confirm_step.skill, needs_confirm_step.args)
            await self.feedback.notify(
                "Підтвердіть дію (Enter / Esc)",
                prompt_msg,
            )
            return

        await self._execute_intent(intent)

    async def _on_confirm(self) -> None:
        """Підтвердження очікуваної ризикової дії клавішею Enter."""
        if self._state != AssistantState.CONFIRMING or self._pending_intent is None:
            return
        intent = self._pending_intent
        self._pending_intent = None
        intent.args["_confirmed"] = True
        if intent.steps:
            for s in intent.steps:
                s.args["_confirmed"] = True
        await self._execute_intent(intent)

    async def _execute_intent(self, intent: CommandIntent) -> None:
        """Виконує навичку з реєстру та повертає автомат у стан IDLE (підтримує ланцюжки дій steps)."""
        self.transition(AssistantState.EXECUTING)
        try:
            steps = intent.steps if intent.steps else [intent]
            for idx, step in enumerate(steps):
                validate_intent_safety(step.skill, step.args)

                if step.skill == "gesture.drag" and self.registry.get("gesture.drag") is None:
                    ctrl = self._ensure_gesture_controller()
                    self.registry.register(GestureDragSkill(ctrl))

                skill = self.registry.get(step.skill)
                if skill is not None:
                    log.info("Виконання кроку %d/%d: %s (%s)", idx + 1, len(steps), step.skill, step.args)
                    await skill.execute(step.args)
                    # Якщо відкрили програму/файл/сайт, а наступний крок маніпулює вікном —
                    # даємо системі невелику паузу (0.6 с) для появи нового вікна та передачі фокусу
                    if (
                        len(steps) > idx + 1
                        and step.skill in ("app.launch", "file.open", "file.create", "web.search")
                        and steps[idx + 1].skill in ("window.move", "window.resize", "window.focus")
                    ):
                        await asyncio.sleep(0.6)
                else:
                    log.info("Навичку '%s' розпізнано, але ще не підключено до реєстру.", step.skill)
        except Exception as exc:
            log.exception("Помилка виконання навички %s: %s", intent.skill, exc)
            await self.feedback.notify(f"Помилка {intent.skill}", str(exc))
        finally:
            self.transition(AssistantState.IDLE)

    def _ensure_gesture_controller(self) -> GestureRuntimeController:
        if self.gesture_controller is None:
            self.gesture_controller = GestureRuntimeController(
                config=self.config.gesture,
                window_manager=WindowManager(),
            )
        return self.gesture_controller

    async def _on_gesture_press(self) -> None:
        """Перемикає стан відстеження жестів (toggle: увімкнути / вимкнути)."""
        ctrl = self._ensure_gesture_controller()
        if ctrl.is_running:
            log.info("Перемикання жестів: зупинка контролера")
            await asyncio.to_thread(ctrl.stop, False)
        else:
            log.info("Перемикання жестів: запуск контролера")
            await asyncio.to_thread(ctrl.start)

    async def _on_gesture_release(self) -> None:
        """У режимі toggle відпускання клавіші не зупиняє відстеження."""
        pass

    async def _on_cancel(self) -> None:
        """Скасування запису, перетягування вікна або поточної дії по Esc."""
        if self.gesture_controller is not None and self.gesture_controller.is_running:
            await asyncio.to_thread(self.gesture_controller.stop, True)
        elif self.gesture_controller is not None and self.gesture_controller.mover.active_session is not None:
            await asyncio.to_thread(self.gesture_controller.mover.cancel_and_restore)

        if self.recorder.recording:
            await asyncio.to_thread(self.recorder.stop)
        self._pending_intent = None
        if self._state != AssistantState.IDLE:
            log.info("Дію скасовано користувачем (Esc).")
            self.transition(AssistantState.IDLE)

    async def _safe_warmup_whisper(self) -> None:
        """Безпечний прогрів Faster-Whisper без падіння основного процесу при помилці завантаження."""
        if not isinstance(self.transcriber, WhisperLocalTranscriber):
            return
        try:
            await self.transcriber.warmup()
        except Exception as exc:
            log.exception("Не вдалося ініціалізувати Faster-Whisper: %s", exc)
            await self.feedback.notify("Помилка Faster-Whisper", str(exc))
            if self.config.secrets.gemini_api_key.strip():
                log.warning("Перемикання STT на резервний GeminiTranscriber.")
                self.transcriber = GeminiTranscriber(
                    config=self.config.stt,
                    api_key=self.config.secrets.gemini_api_key,
                )

    async def run(self, once: bool = False) -> None:
        """Запускає прогрів STT (для whisper_local), слухач клавіш та цикл обробки подій."""
        log.info(
            "FRIDAY запущено (стан=%s, stt=%s, hotkey=%s)",
            self._state.value,
            self.config.stt.backend,
            self.config.hotkeys.voice,
        )
        if once:
            log.info("Режим перевірки (--once): завершення роботи.")
            return

        loop = asyncio.get_running_loop()
        if self._listener_factory is not None:
            self._hotkey_listener = self._listener_factory(self.config.hotkeys, loop, self.event_queue)
        else:
            self._hotkey_listener = HotkeyListener(self.config.hotkeys, loop, self.event_queue)

        self._hotkey_listener.start()
        warmup_task: asyncio.Task[None] | None = None
        if isinstance(self.transcriber, WhisperLocalTranscriber):
            warmup_task = asyncio.create_task(self._safe_warmup_whisper())
        try:
            while not self._stop_event.is_set():
                try:
                    event = await asyncio.wait_for(self.event_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue

                try:
                    await self.handle_event(event)
                except Exception as exc:
                    log.exception("Помилка обробки події %s: %s", event.type, exc)
                    self.transition(AssistantState.IDLE)
                    await self.feedback.notify("Помилка FRIDAY", str(exc))
        except asyncio.CancelledError:
            log.info("Отримано сигнал скасування.")
        finally:
            if warmup_task is not None and not warmup_task.done():
                warmup_task.cancel()
            if self._hotkey_listener is not None:
                self._hotkey_listener.stop()
            if self.gesture_controller is not None and self.gesture_controller.is_running:
                await asyncio.to_thread(self.gesture_controller.stop, True)
            if self.recorder.recording:
                await asyncio.to_thread(self.recorder.stop)
            self.transition(AssistantState.IDLE)
            log.info("FRIDAY зупинено.")
