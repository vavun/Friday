"""Bidirectional voice & HTTP bridge between FRIDAY and Jarvis."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

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
        """Speak to Jarvis, invoke Jarvis over HTTP (or fallback), and optionally continue dialogue."""
        raw_message = str(args.get("message", "")).strip()
        spoken_intro = str(args.get("spoken_intro", "")).strip()
        discuss = bool(args.get("discuss", False))

        friday_callout = spoken_intro or await self._build_friday_callout(raw_message)
        log.info("🗣 Friday calling Jarvis: %s", friday_callout)
        await self.app.feedback.notify("FRIDAY -> Jarvis", friday_callout)
        await self.app.speak_and_wait(friday_callout)

        url = f"{self.config.bridge.jarvis_url.rstrip('/')}/api/assist"
        payload = {
            "sender": "friday",
            "message": friday_callout,
            "raw_user_request": raw_message,
            "speak": True,
            "turn": 1,
        }

        jarvis_reply = ""
        try:
            resp = await asyncio.to_thread(
                self._http_post,
                url,
                payload,
                self.config.bridge.timeout_s,
            )
            jarvis_reply = str(resp.get("reply", "")).strip()
            log.info("🤖 Jarvis replied via bridge: %s", jarvis_reply)
        except Exception as exc:
            log.info("Jarvis bridge offline (%s); using direct male-voice fallback", exc)
            jarvis_reply = await self._generate_jarvis_fallback_reply(friday_callout)
            await self.app.feedback.notify("Jarvis -> FRIDAY", jarvis_reply)
            await self._speak_as_jarvis(jarvis_reply)

        followup = ""
        if discuss and jarvis_reply:
            followup = await self._generate_friday_followup(friday_callout, jarvis_reply)
            if followup:
                await self.app.feedback.notify("FRIDAY -> Jarvis", followup)
                await self.app.speak_and_wait(followup)

        return {
            "status": "ok",
            "friday_said": friday_callout,
            "jarvis_reply": jarvis_reply,
            "friday_followup": followup,
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
            try:
                pcm = await self._jarvis_tts.synthesize_pcm(sent)
                if pcm:
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

    async def _generate_friday_followup(self, friday_callout: str, jarvis_reply: str) -> str:
        """Generate a brief collaborative follow-up from Friday after Jarvis speaks."""
        prompt = (
            f"You (FRIDAY) asked your partner Jarvis: {friday_callout!r}\n"
            f"Jarvis replied: {jarvis_reply!r}\n"
            "Give a 1-sentence natural follow-up or agreement addressed to Jarvis and the user."
        )
        return await self._call_gemini(FRIDAY_CHAT_SYSTEM_PROMPT, prompt)

    async def _generate_jarvis_fallback_reply(self, friday_callout: str) -> str:
        """Generate Jarvis's reply when Jarvis's standalone process is not running."""
        prompt = (
            f"Your partner assistant FRIDAY just said to you: {friday_callout!r}. "
            "Respond directly to FRIDAY and the user in 1-3 concise spoken sentences."
        )
        res = await self._call_gemini(JARVIS_FALLBACK_SYSTEM_PROMPT, prompt)
        return res or "I hear you, Friday. I'm ready to assist."

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
