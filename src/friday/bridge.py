"""Bidirectional voice & HTTP bridge between FRIDAY and Jarvis."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from friday.tts import clear_queue
from friday.tts.engine import EdgeTtsEngine
from friday.tts.sanitizer import sanitize_for_tts, split_sentences

if TYPE_CHECKING:
    from friday.app import FridayApp

log = logging.getLogger("friday.bridge")

FRIDAY_CHAT_SYSTEM_PROMPT = (
    "You are FRIDAY, an intelligent female AI assistant running on the user's Windows laptop. "
    "You work side-by-side with your partner AI assistant Jarvis (male voice). "
    "You specialize in desktop window management, multi-monitor layouts, hand gesture control, "
    "file organization, and Telegram messaging, while Jarvis specializes in the Knowledge Vault, "
    "web research (Box), and coding (Omni). "
    "Reply directly in natural spoken English in 1-2 concise sentences without markdown or code blocks."
)

JARVIS_FALLBACK_SYSTEM_PROMPT = (
    "You are Jarvis, an intelligent male AI assistant running on the user's laptop alongside "
    "your partner AI assistant FRIDAY. FRIDAY just spoke to you and asked for your help or opinion. "
    "Reply directly to FRIDAY (and the user) in natural spoken English in 1-3 concise sentences, "
    "without markdown or code blocks."
)

CONCLUSION_PATTERNS = [
    r"\b(we're|we are) all set\b",
    r"\ball set\b",
    r"\bthat covers (it|everything|all)\b",
    r"\bthat's all (for now|from me)\b",
    r"\bnothing (more|else) to add\b",
    r"\bwe('re| are) good to go\b",
    r"\bgood to go\b",
    r"\b(sounds|sound) like a plan\b",
    r"\bthat wraps (it|everything) up\b",
    r"\btalk (to you )?soon\b",
    r"\bhave a great (day|rest of your day)\b",
    r"\bover and out\b",
    r"\bglad (we|I) could help\b",
    r"\[DONE\]",
]


def is_dialogue_concluded(text: str) -> bool:
    """Return True if the text signals natural dialogue conclusion without open questions."""
    if not text:
        return True
    cleaned = text.strip()
    if cleaned.endswith("?"):
        return False
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if lines and lines[-1].endswith("?"):
        return False
    last_sent = re.split(r"[.!?]\s+", cleaned)[-1].strip()
    if last_sent.endswith("?"):
        return False

    lower = cleaned.lower()
    for pat in CONCLUSION_PATTERNS:
        if re.search(pat, lower, re.IGNORECASE):
            return True
    return False


def _http_post_json(url: str, payload: dict[str, Any], timeout_s: float = 45.0) -> dict[str, Any]:
    """Send synchronous JSON POST request (run via asyncio.to_thread)."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


class PeerBridgeService:
    """Manages HTTP server for Jarvis->Friday requests and client for Friday->Jarvis requests."""

    def __init__(self, app: FridayApp, http_post_fn: Any | None = None) -> None:
        self.app = app
        self.config = app.config
        self._server: asyncio.AbstractServer | None = None
        self._http_post = http_post_fn or _http_post_json
        self._jarvis_tts: EdgeTtsEngine | None = None
        self._dialogue_active: bool = False
        self._dialogue_cancelled: bool = False

    def cancel_dialogue(self) -> None:
        """Mark active peer dialogue as cancelled and halt further turns."""
        self._dialogue_cancelled = True
        self._dialogue_active = False
        log.info("🛑 Friday peer dialogue cancelled.")

    def reset_dialogue(self) -> None:
        """Reset dialogue cancellation state for a new session."""
        self._dialogue_cancelled = False
        self._dialogue_active = True

    async def notify_jarvis_cancel(self) -> None:
        """Send cancel signal to Jarvis bridge over HTTP."""
        url = f"{self.config.bridge.jarvis_url.rstrip('/')}/api/cancel"
        try:
            await asyncio.to_thread(self._http_post, url, {"source": "friday"}, 2.0)
        except Exception:
            pass

    async def start(self) -> None:
        """Start local HTTP server for incoming Jarvis calls."""
        if not self.config.bridge.enabled or self._server is not None:
            return
        try:
            self._server = await asyncio.start_server(
                self._handle_client,
                host=self.config.bridge.friday_host,
                port=self.config.bridge.friday_port,
            )
            log.info(
                "Friday bridge server listening on http://%s:%d",
                self.config.bridge.friday_host,
                self.config.bridge.friday_port,
            )
        except OSError as exc:
            log.warning("Could not bind Friday bridge port %d: %s", self.config.bridge.friday_port, exc)

    async def stop(self) -> None:
        """Stop local HTTP server."""
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle minimal HTTP/1.1 JSON requests."""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not request_line:
                return
            parts = request_line.decode("utf-8", errors="replace").strip().split(" ")
            method = parts[0].upper() if parts else "GET"
            path = parts[1] if len(parts) > 1 else "/"

            content_length = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if not line or line in (b"\r\n", b"\n"):
                    break
                header_str = line.decode("utf-8", errors="replace").strip()
                if header_str.lower().startswith("content-length:"):
                    content_length = int(header_str.split(":", 1)[1].strip())

            body_bytes = b""
            if content_length > 0:
                body_bytes = await asyncio.wait_for(reader.readexactly(content_length), timeout=10.0)

            if method == "GET" and path == "/api/health":
                await self._send_json(writer, 200, {"status": "ok", "assistant": "friday"})
                return

            if method == "POST" and path == "/api/cancel":
                log.info("Received cancel request from peer Jarvis")
                self.cancel_dialogue()
                for t in list(self.app._speak_tasks):
                    if not t.done():
                        t.cancel()
                self.app._speak_tasks.clear()
                clear_queue(self.app.audio_queue)
                if self.app.audio_player is not None:
                    self.app.audio_player.abort()
                await self._send_json(writer, 200, {"status": "cancelled", "assistant": "friday"})
                return

            if method == "POST" and path == "/api/assist":
                payload = json.loads(body_bytes.decode("utf-8", errors="replace")) if body_bytes else {}
                result = await self.handle_incoming_from_jarvis(payload)
                await self._send_json(writer, 200, result)
                return

            await self._send_json(writer, 404, {"error": "Not found"})
        except Exception as exc:
            log.exception("Bridge request error: %s", exc)
            try:
                await self._send_json(writer, 500, {"error": str(exc)})
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _send_json(self, writer: asyncio.StreamWriter, status: int, data: dict[str, Any]) -> None:
        reason = "OK" if status == 200 else "Error"
        body = json.dumps(data).encode("utf-8")
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(headers + body)
        await writer.drain()

    async def handle_incoming_from_jarvis(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process a request sent by Jarvis, execute desktop action or reply, and speak aloud."""
        message = str(payload.get("message", "")).strip()
        speak = bool(payload.get("speak", True))
        if not message:
            return {"ok": False, "reply": "No message provided.", "spoken": False}

        log.info("🤝 Incoming request from Jarvis: %s", message)
        intent = await self.app.router.route(message)

        if intent.skill not in ("clarify", "assistant.jarvis"):
            exec_summary = await self.app.execute_intent_from_peer(intent)
            reply = f"Sure Jarvis, {exec_summary.lower()}" if exec_summary else "Sure Jarvis, I took care of that."
            if speak:
                await self.app.speak_and_wait(reply)
            return {"ok": True, "reply": reply, "spoken": speak, "skill": intent.skill}

        reply = await self._generate_friday_reply_to_jarvis(message)
        if speak:
            await self.app.speak_and_wait(reply)
        return {"ok": True, "reply": reply, "spoken": speak, "skill": "conversation"}

    async def call_jarvis(self, args: dict[str, Any]) -> dict[str, Any]:
        """Speak to Jarvis, invoke Jarvis over HTTP (or fallback), and converse continuously until concluded or cancelled."""
        raw_message = str(args.get("message", "")).strip()
        spoken_intro = str(args.get("spoken_intro", "")).strip()
        discuss = bool(args.get("discuss", False))

        self.reset_dialogue()
        friday_callout = spoken_intro or await self._build_friday_callout(raw_message)
        log.info("🗣 Friday calling Jarvis: %s", friday_callout)
        await self.app.feedback.notify("FRIDAY -> Jarvis", friday_callout)
        await self.app.speak_and_wait(friday_callout)

        if self._dialogue_cancelled:
            self._dialogue_active = False
            return {
                "status": "cancelled",
                "friday_said": friday_callout,
                "jarvis_reply": "",
                "friday_followup": "",
                "jarvis_followup_reply": "",
            }

        url = f"{self.config.bridge.jarvis_url.rstrip('/')}/api/assist"
        max_turns = self.config.bridge.max_discussion_turns if discuss else 1
        turn = 1
        current_friday_message = friday_callout
        jarvis_reply = ""
        followup = ""
        jarvis_followup_reply = ""
        dialogue_history: list[dict[str, str]] = [
            {"role": "friday", "content": friday_callout}
        ]

        while self._dialogue_active and not self._dialogue_cancelled and turn <= max_turns:
            log.info("--- Bridge dialogue turn %d/%d (discuss=%s) ---", turn, max_turns, discuss)
            payload = {
                "sender": "friday",
                "message": current_friday_message,
                "raw_user_request": raw_message,
                "speak": True,
                "turn": turn,
                "discuss": discuss,
            }

            reply_text = ""
            try:
                resp = await asyncio.to_thread(
                    self._http_post,
                    url,
                    payload,
                    self.config.bridge.timeout_s,
                )
                reply_text = str(resp.get("reply", "")).strip()
                log.info("🤖 Jarvis replied via bridge (turn %d): %s", turn, reply_text)
            except Exception as exc:
                log.info("Jarvis bridge offline (%s); using direct male-voice fallback", exc)
                try:
                    reply_text = await self._generate_jarvis_fallback_reply(
                        current_friday_message,
                        dialogue_history=dialogue_history,
                        raw_topic=raw_message,
                    )
                except TypeError:
                    reply_text = await self._generate_jarvis_fallback_reply(current_friday_message)
                if self._dialogue_cancelled:
                    break
                await self.app.feedback.notify("Jarvis -> FRIDAY", reply_text)
                await self._speak_as_jarvis(reply_text)

            if turn == 1:
                jarvis_reply = reply_text
            else:
                jarvis_followup_reply = reply_text

            if self._dialogue_cancelled or not reply_text:
                break

            dialogue_history.append({"role": "jarvis", "content": reply_text})

            # Check if Jarvis signaled natural conclusion
            if is_dialogue_concluded(reply_text):
                log.info("🛑 Dialogue concluded by Jarvis: %s", reply_text)
                break

            # If not in continuous discussion mode or reached max turns, stop
            if not discuss or turn >= max_turns:
                break

            # Natural pause between speakers so speech feels natural and unhurried
            await asyncio.sleep(0.5)
            if self._dialogue_cancelled:
                break

            # Friday generates next conversational response
            followup = await self._generate_friday_dialogue_turn(
                dialogue_history=dialogue_history,
                raw_topic=raw_message,
                turn=turn,
                max_turns=max_turns,
            )

            if self._dialogue_cancelled or not followup:
                break

            dialogue_history.append({"role": "friday", "content": followup})
            log.info("🗣 Friday follow-up (turn %d): %s", turn + 1, followup)
            await self.app.feedback.notify("FRIDAY -> Jarvis", followup)
            await self.app.speak_and_wait(followup)

            if self._dialogue_cancelled:
                break

            # Check if Friday decided to conclude
            if is_dialogue_concluded(followup):
                log.info("🛑 Dialogue concluded by Friday: %s", followup)
                try:
                    final_payload = {
                        "sender": "friday",
                        "message": followup,
                        "speak": False,
                        "turn": turn + 1,
                        "concluded": True,
                    }
                    await asyncio.to_thread(self._http_post, url, final_payload, 5.0)
                except Exception:
                    pass
                break

            current_friday_message = followup
            turn += 1
            await asyncio.sleep(0.5)

        self._dialogue_active = False
        return {
            "status": "cancelled" if self._dialogue_cancelled else "ok",
            "friday_said": friday_callout,
            "jarvis_reply": jarvis_reply,
            "friday_followup": followup,
            "jarvis_followup_reply": jarvis_followup_reply,
            "turns_completed": turn,
            "history": dialogue_history,
        }

    async def _speak_as_jarvis(self, text: str) -> None:
        """Synthesize and play speech using Jarvis's male voice when Jarvis process is offline."""
        if not self.config.tts.enabled:
            return
        clean = sanitize_for_tts(text)
        if not clean:
            return
        if self._jarvis_tts is None:
            self._jarvis_tts = EdgeTtsEngine(
                self.config.tts.model_copy(update={"voice": self.config.bridge.jarvis_voice})
            )
        for sent in split_sentences(clean):
            if self._dialogue_cancelled:
                break
            try:
                pcm = await self._jarvis_tts.synthesize_pcm(sent)
                if pcm and not self._dialogue_cancelled:
                    await self.app.audio_queue.put(pcm)
            except Exception as exc:
                log.warning("Jarvis fallback TTS failed: %s", exc)
        await self.app.wait_for_audio()

    async def _build_friday_callout(self, raw_message: str) -> str:
        """Convert user utterance into a natural English sentence from Friday to Jarvis."""
        cleaned = raw_message.strip()
        if not cleaned:
            return "Hey Jarvis, are you there?"

        # If already a clean English sentence without Cyrillic, address Jarvis directly
        has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in cleaned)
        if not has_cyrillic:
            lowered = cleaned.lower()
            for prefix in (
                "ask jarvis to ",
                "ask jarvis ",
                "tell jarvis to ",
                "tell jarvis ",
                "call jarvis and ",
                "call jarvis ",
                "talk to jarvis about ",
                "discuss with jarvis ",
            ):
                if lowered.startswith(prefix):
                    rest = cleaned[len(prefix) :].strip()
                    return f"Hey Jarvis, could you help with this: {rest}"
            if "jarvis" not in lowered:
                return f"Hey Jarvis, {cleaned}"
            return cleaned

        prompt = (
            "The user asked you (FRIDAY) to speak to your partner AI assistant Jarvis: "
            f"{cleaned!r}. "
            "Write ONLY the short 1-sentence message in English that you (FRIDAY) say aloud directly to Jarvis "
            "(for example: 'Hey Jarvis, could you search the vault for project notes?')."
        )
        res = await self._call_gemini(FRIDAY_CHAT_SYSTEM_PROMPT, prompt)
        return res or "Hey Jarvis, could you help us with this request?"

    async def _generate_friday_reply_to_jarvis(self, jarvis_message: str) -> str:
        """Generate Friday's spoken reply when Jarvis asks her a question."""
        prompt = (
            f"Your partner AI assistant Jarvis just said to you: {jarvis_message!r}. "
            "Reply directly to Jarvis in 1-2 concise spoken English sentences."
        )
        res = await self._call_gemini(FRIDAY_CHAT_SYSTEM_PROMPT, prompt)
        return res or "I'm right here, Jarvis, and ready to help."

    async def _generate_friday_dialogue_turn(
        self,
        dialogue_history: list[dict[str, str]],
        raw_topic: str,
        turn: int,
        max_turns: int,
    ) -> str:
        """Generate Friday's next conversational turn addressed to Jarvis."""
        history_lines = []
        for entry in dialogue_history[-6:]:
            speaker = "FRIDAY" if entry["role"] == "friday" else "Jarvis"
            history_lines.append(f"{speaker}: {entry['content']}")
        history_str = "\n".join(history_lines)

        wrap_up = ""
        if turn >= max_turns - 1:
            wrap_up = (
                "This is the final turn. Give a natural sign-off acknowledging Jarvis "
                "(e.g., 'Sounds like a plan, Jarvis. We are all set.' or 'That covers everything, thank you.')."
            )
        else:
            wrap_up = (
                "Continue the conversation naturally in 1-2 spoken sentences. Share your perspective or ask a follow-up. "
                "If the topic is resolved and everything is clear, wrap up with 'That covers everything' or 'We are all set'."
            )

        prompt = (
            f"You are FRIDAY in an ongoing verbal discussion with your partner AI assistant Jarvis.\n"
            f"User topic: {raw_topic!r}\n\n"
            f"Recent dialogue:\n{history_str}\n\n"
            f"{wrap_up}\n"
            "Speak directly to Jarvis in 1-2 natural sentences without markdown or code."
        )
        res = await self._call_gemini(FRIDAY_CHAT_SYSTEM_PROMPT, prompt)
        return res or "Sounds like a plan, Jarvis. We're all set."

    async def _generate_friday_followup(self, friday_callout: str, jarvis_reply: str) -> str:
        """Backwards compatibility wrapper for generating a follow-up."""
        history = [
            {"role": "friday", "content": friday_callout},
            {"role": "jarvis", "content": jarvis_reply},
        ]
        return await self._generate_friday_dialogue_turn(history, friday_callout, turn=1, max_turns=10)

    async def _generate_jarvis_fallback_reply(
        self,
        friday_callout: str,
        dialogue_history: list[dict[str, str]] | None = None,
        raw_topic: str = "",
    ) -> str:
        """Generate Jarvis's reply when Jarvis's standalone process is not running."""
        history_lines = []
        if dialogue_history:
            for entry in dialogue_history[-6:]:
                speaker = "FRIDAY" if entry["role"] == "friday" else "Jarvis"
                history_lines.append(f"{speaker}: {entry['content']}")
        history_str = "\n".join(history_lines) if history_lines else f"FRIDAY: {friday_callout}"

        prompt = (
            f"You are Jarvis in an ongoing spoken dialogue with your partner assistant FRIDAY.\n"
            f"User topic: {raw_topic or friday_callout!r}\n\n"
            f"Recent dialogue:\n{history_str}\n\n"
            "FRIDAY just spoke the last line. Respond directly to FRIDAY and the user in 1-2 concise spoken sentences. "
            "If the topic is resolved, sign off with 'That covers everything' or 'We are all set'."
        )
        res = await self._call_gemini(JARVIS_FALLBACK_SYSTEM_PROMPT, prompt)
        return res or "I hear you, Friday. We're all set."

    async def _call_gemini(self, system_instruction: str, user_prompt: str) -> str:
        """Call Gemini Flash-Lite for quick conversational generation."""
        api_key = self.config.secrets.gemini_api_key.strip()
        if not api_key:
            return ""
        try:
            from google import genai
            from google.genai import types

            def _sync_call() -> str:
                client = genai.Client(api_key=api_key)
                resp = client.models.generate_content(
                    model=self.config.llm.model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.4,
                    ),
                )
                return (resp.text or "").strip().strip("\"'")

            return await asyncio.to_thread(_sync_call)
        except Exception as exc:
            log.warning("Bridge Gemini call failed: %s", exc)
            return ""


class JarvisBridgeSkill:
    """Skill `assistant.jarvis` allowing FRIDAY to call and converse with Jarvis."""

    name = "assistant.jarvis"

    def __init__(self, bridge: PeerBridgeService) -> None:
        self.bridge = bridge

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        task = asyncio.create_task(self.bridge.call_jarvis(args))
        self.bridge.app._speak_tasks.add(task)
        task.add_done_callback(self.bridge.app._speak_tasks.discard)
        return {"status": "started"}
