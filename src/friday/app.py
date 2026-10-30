"""Автомат станів та головна оркестрація асистента FRIDAY."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import StrEnum
from typing import Any, Callable

from friday.audio import AudioRecorder
from friday.config import AppConfig
from friday.feedback import FeedbackManager
from friday.gesture.controller import GestureDragSkill, GestureRuntimeController
from friday.hotkeys import HotkeyEvent, HotkeyEventType, HotkeyListener
from friday.launcher.services.config_store import ConfigStore
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
from friday.skills.dynamic import (
    AppDiscoverSkill,
    AppDiscoveryService,
    DynamicSkillManager,
    PipelineSaveSkill,
    SkillCreateSkill,
    SkillRunSkill,
)
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
from friday.tts import (
    AudioPlayer,
    TtsEngine,
    clear_queue,
    create_tts_engine,
)

log = logging.getLogger("friday.app")


class AssistantState(StrEnum):
    """Стани автомата FRIDAY."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    ROUTING = "ROUTING"
    CONFIRMING = "CONFIRMING"
    EXECUTING = "EXECUTING"


def build_spoken_confirmation(skill: str, args: dict[str, Any]) -> str:
    """English confirmation prompt for TTS."""
    if skill == "telegram.send":
        contact = str(args.get("contact", "")).strip()
        return f"Send message to {contact}?" if contact else "Send this message?"
    if skill == "agent.run":
        return "Start agent session?"
    if skill == "skill.create":
        name = str(args.get("name") or args.get("trigger") or "skill").strip()
        return f"Create and run skill {name}?"
    if skill == "pipeline.save":
        name = str(args.get("name") or args.get("trigger") or "pipeline").strip()
        return f"Save pipeline {name}?"
    return f"Confirm {skill}?"


class FridayApp:
    """Assistant state machine and orchestration."""

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
        tts_engine: TtsEngine | None = None,
        audio_player: AudioPlayer | None = None,
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

        self.audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
        self.audio_player: AudioPlayer | None = audio_player
        if tts_engine is not None:
            self.tts_engine: TtsEngine | None = tts_engine
        elif self.config.tts.enabled and self.config.tts.engine != "off":
            self.tts_engine = create_tts_engine(self.config.tts)
        else:
            self.tts_engine = None
        self._playback_task: asyncio.Task[None] | None = None
        self._speak_tasks: set[asyncio.Task[None]] = set()

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
        config_store = ConfigStore()
        app_discovery = AppDiscoveryService(
            config=self.config,
            app_launcher=app_launcher,
            config_store=config_store,
        )
        reg.register(AppDiscoverSkill(app_discovery, app_launcher))
        if hasattr(self.router, "llm_parser") and self.router.llm_parser is not None:
            self.router.llm_parser.app_discovery = app_discovery

        dynamic_mgr = DynamicSkillManager(
            config=self.config,
            registry=reg,
            config_store=config_store,
            app_discovery=app_discovery,
        )
        reg.register(PipelineSaveSkill(dynamic_mgr))
        reg.register(SkillCreateSkill(dynamic_mgr))
        reg.register(SkillRunSkill(dynamic_mgr))
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

    async def speak(self, text: str) -> None:
        """Synthesize text and enqueue PCM audio."""
        if not self.config.tts.enabled or self.tts_engine is None:
            return
        if not text or not text.strip():
            return
        try:
            pcm = await self.tts_engine.synthesize_pcm(text)
            if pcm:
                await self.audio_queue.put(pcm)
        except Exception as exc:
            log.warning("TTS synthesis failed: %s", exc)

    def _speak_nowait(self, text: str) -> asyncio.Task[None] | None:
        """Enqueue speech in background task."""
        if not self.config.tts.enabled or self.tts_engine is None:
            return None
        if (self._playback_task is not None and not self._playback_task.done()) or self.audio_player is not None:
            task = asyncio.create_task(self.speak(text))
            self._speak_tasks.add(task)
            task.add_done_callback(self._speak_tasks.discard)
            return task
        return None

    async def _playback_loop(self) -> None:
        """Play audio chunks sequentially from queue."""
        if self.audio_player is None and self.config.tts.enabled and self.config.tts.engine != "off":
            try:
                self.audio_player = AudioPlayer(sample_rate=self.config.tts.sample_rate)
            except Exception as exc:
                log.warning("Audio player initialization failed: %s", exc)

        try:
            while not self._stop_event.is_set():
                try:
                    chunk = await asyncio.wait_for(self.audio_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                if chunk is None:
                    break
                try:
                    if self.audio_player is not None:
                        await asyncio.to_thread(self.audio_player.play_audio, chunk)
                except Exception:
                    log.exception("Audio playback error")
                finally:
                    self.audio_queue.task_done()
        finally:
            if self.audio_player is not None:
                await asyncio.to_thread(self.audio_player.close)
                self.audio_player = None

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
        """Start recording on voice hotkey."""
        # Barge-in: cancel speaking tasks and drain audio
        for t in list(self._speak_tasks):
            if not t.done():
                t.cancel()
        self._speak_tasks.clear()
        clear_queue(self.audio_queue)
        if self.audio_player is not None:
            self.audio_player.abort()

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
        """Finish recording and transcribe speech."""
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
            self._speak_nowait("Sorry, could not recognize that.")
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
        """Route recognized text and dispatch action."""
        self.transition(AssistantState.ROUTING)
        intent = await self.router.route(text)
        self._last_intent = intent

        if intent.skill == "clarify":
            question = str(intent.args.get("question", "Could you clarify the command, please?"))
            log.info("❓ Потрібне уточнення: %s", question)
            self.transition(AssistantState.IDLE)
            await self.feedback.notify("Уточніть команду", question)
            self._speak_nowait(question)
            return

        # Check safety confirmation requirements
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
            spoken = build_spoken_confirmation(needs_confirm_step.skill, needs_confirm_step.args)
            self._speak_nowait(spoken)
            return

        await self._execute_intent(intent)

    async def _on_confirm(self) -> None:
        """Confirm pending action on Enter."""
        if self._state != AssistantState.CONFIRMING or self._pending_intent is None:
            return
        intent = self._pending_intent
        self._pending_intent = None
        intent.args["_confirmed"] = True
        if intent.steps:
            for s in intent.steps:
                s.args["_confirmed"] = True
        self._speak_nowait("Confirmed.")
        await self._execute_intent(intent)

    async def _execute_intent(self, intent: CommandIntent) -> None:
        """Виконує навичку з реєстру та повертає автомат у стан IDLE (підтримує ланцюжки дій steps)."""
        self.transition(AssistantState.EXECUTING)
        try:
            steps = intent.steps if intent.steps else [intent]
            last_app_key: str | None = None
            last_raw_app_name: str | None = None
            prev_was_launch = False

            for idx, step in enumerate(steps):
                validate_intent_safety(step.skill, step.args)

                if step.skill == "gesture.drag" and self.registry.get("gesture.drag") is None:
                    ctrl = self._ensure_gesture_controller()
                    self.registry.register(GestureDragSkill(ctrl))

                # Якщо попередній крок відкрив програму через app.discover/app.launch, оновлюємо target для window.*
                if step.skill in ("window.move", "window.resize", "window.focus") and last_app_key:
                    cur_t = str(step.args.get("target", "active")).strip().lower()
                    if cur_t in ("його", "її", "це", "це вікно", "") or (
                        last_raw_app_name and cur_t == last_raw_app_name
                    ):
                        step.args["target"] = last_app_key
                    elif cur_t in self.config.app_synonyms:
                        step.args["target"] = self.config.app_synonyms[cur_t]

                skill = self.registry.get(step.skill)
                if skill is not None:
                    log.info("Виконання кроку %d/%d: %s (%s)", idx + 1, len(steps), step.skill, step.args)
                    if step.skill in ("app.launch", "app.discover"):
                        last_raw_app_name = str(step.args.get("name", "")).strip().lower()

                    # Якщо це крок керування вікном одразу після запуску програми, даємо до 3 коротких спроб знайти нове вікно
                    if prev_was_launch and step.skill in ("window.move", "window.resize", "window.focus"):
                        for wait_try in range(3):
                            try:
                                res = await skill.execute(step.args)
                                break
                            except LookupError:
                                if wait_try == 2:
                                    raise
                                await asyncio.sleep(0.25)
                    else:
                        res = await skill.execute(step.args)

                    if isinstance(res, dict) and res.get("app"):
                        last_app_key = str(res["app"]).strip().lower()
                    elif step.skill == "web.search":
                        last_app_key = "browser"

                    prev_was_launch = step.skill in (
                        "app.launch",
                        "app.discover",
                        "file.open",
                        "file.create",
                        "web.search",
                    )
                    # Якщо відкрили програму/файл/сайт, а наступний крок маніпулює вікном —
                    # даємо системі невелику паузу (0.6 с) для появи нового вікна та передачі фокусу
                    if (
                        len(steps) > idx + 1
                        and prev_was_launch
                        and steps[idx + 1].skill in ("window.move", "window.resize", "window.focus")
                    ):
                        await asyncio.sleep(0.6)
                else:
                    log.info("Навичку '%s' розпізнано, але ще не підключено до реєстру.", step.skill)
        except Exception as exc:
            log.exception("Помилка виконання навички %s: %s", intent.skill, exc)
            await self.feedback.notify(f"Помилка {intent.skill}", str(exc))
            self._speak_nowait(f"Error executing {intent.skill}.")
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
        """Cancel current operation on Esc."""
        for t in list(self._speak_tasks):
            if not t.done():
                t.cancel()
        self._speak_tasks.clear()
        clear_queue(self.audio_queue)
        if self.audio_player is not None:
            self.audio_player.abort()

        if self.gesture_controller is not None and self.gesture_controller.is_running:
            await asyncio.to_thread(self.gesture_controller.stop, True)
        elif self.gesture_controller is not None and self.gesture_controller.mover.active_session is not None:
            await asyncio.to_thread(self.gesture_controller.mover.cancel_and_restore)

        if self.recorder.recording:
            await asyncio.to_thread(self.recorder.stop)
        was_confirming = self._state == AssistantState.CONFIRMING
        self._pending_intent = None
        if self._state != AssistantState.IDLE:
            log.info("Дію скасовано користувачем (Esc).")
            self.transition(AssistantState.IDLE)
            if was_confirming:
                self._speak_nowait("Cancelled.")

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
        """Run STT warmup, hotkey listener, audio playback and event loop."""
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
        playback_task: asyncio.Task[None] | None = None
        if self.config.tts.enabled and self.config.tts.engine != "off":
            playback_task = asyncio.create_task(self._playback_loop())
            self._playback_task = playback_task
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
            for t in list(self._speak_tasks):
                if not t.done():
                    t.cancel()
            self._speak_tasks.clear()
            clear_queue(self.audio_queue)
            await self.audio_queue.put(None)
            if playback_task is not None and not playback_task.done():
                try:
                    await asyncio.wait_for(playback_task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    playback_task.cancel()
            self._playback_task = None
            if self._hotkey_listener is not None:
                self._hotkey_listener.stop()
            if self.gesture_controller is not None and self.gesture_controller.is_running:
                await asyncio.to_thread(self.gesture_controller.stop, True)
            if self.recorder.recording:
                await asyncio.to_thread(self.recorder.stop)
            self.transition(AssistantState.IDLE)
            log.info("FRIDAY зупинено.")
