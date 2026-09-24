"""
TTS (Text-to-Speech) Service — Voice output for the legal AI system.

Completes the bidirectional voice dialog:
  User speaks → STT (already exists) → AI processes → TTS → User hears

Engines:
- Microsoft Edge TTS (default, free, no API key needed)
- Azure Cognitive Services TTS (optional, requires API key)
- Fallback stub when edge-tts is not installed

Features:
- Preset voice profiles (formal female, formal male, warm female)
- SSML support for fine-grained prosody control
- Streaming output via async generator
- Graceful degradation when dependencies are missing
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import re
import tempfile
from typing import Any, AsyncGenerator, AsyncIterator

from app.core.config import settings

logger = logging.getLogger(__name__)

# Check whether edge-tts is importable (cached once at module load)
try:
    import edge_tts as _edge_tts_module  # type: ignore
    EDGE_TTS_AVAILABLE = True
except ImportError:
    _edge_tts_module = None
    EDGE_TTS_AVAILABLE = False
    logger.info("edge-tts is not installed; TTS will return stub responses")


# =============================================================================
# Voice presets — curated selections for common legal-AI scenarios
# =============================================================================

class VoicePreset:
    """Named voice presets exposed to API callers."""
    FORMAL_FEMALE = "formal_female"
    FORMAL_MALE = "formal_male"
    WARM_FEMALE = "warm_female"


VOICE_PRESET_MAP: dict[str, dict[str, Any]] = {
    VoicePreset.FORMAL_FEMALE: {
        "voice": "zh-CN-XiaoxiaoNeural",
        "label": "正式女声（晓晓）",
        "gender": "female",
        "style": "专业正式，适合法律文书播报",
        "rate": "+0%",
        "pitch": "+0Hz",
    },
    VoicePreset.FORMAL_MALE: {
        "voice": "zh-CN-YunxiNeural",
        "label": "正式男声（云希）",
        "gender": "male",
        "style": "专业沉稳，适合法律条款解读",
        "rate": "+0%",
        "pitch": "+0Hz",
    },
    VoicePreset.WARM_FEMALE: {
        "voice": "zh-CN-XiaoyiNeural",
        "label": "温暖女声（晓伊）",
        "gender": "female",
        "style": "温暖亲切，适合日常法律咨询",
        "rate": "-5%",
        "pitch": "+0Hz",
    },
}

# Default preset when none is specified
DEFAULT_VOICE_PRESET = VoicePreset.WARM_FEMALE

# A real edge-tts / Azure voice name looks like ``zh-CN-XiaoxiaoNeural``.
# Preset ids such as ``female_formal`` deliberately do NOT match this pattern.
_VOICE_NAME_RE = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,4}-[A-Za-z0-9]+Neural$")


class TTSService:
    """Text-to-Speech service with multiple engine support."""

    def __init__(self) -> None:
        self._engine: str = getattr(settings, "TTS_ENGINE", "edge")
        self._voice: str = getattr(settings, "TTS_VOICE", "zh-CN-XiaoxiaoNeural")

    # -----------------------------------------------------------------
    # Voice / preset resolution
    # -----------------------------------------------------------------

    @staticmethod
    def is_valid_voice_name(voice: str | None) -> bool:
        """Return True when *voice* looks like a real TTS voice name.

        Real names match ``<lang>-<REGION>-<Name>Neural`` (e.g.
        ``zh-CN-XiaoxiaoNeural``).  Preset ids such as ``female_formal`` do
        not, which matters because forwarding a preset id straight into
        edge-tts raises and previously produced a silent, zero-byte response.
        """
        if not voice:
            return False
        return bool(_VOICE_NAME_RE.match(voice.strip()))

    def resolve_voice(
        self,
        voice: str | None,
        preset: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Normalise a caller-supplied ``voice`` / ``preset`` pair.

        Clients (notably the web front-end) frequently pass a *preset id* in
        the ``voice`` field, e.g. ``{"voice": "female_formal"}``.  Passing
        that value through to edge-tts raised an exception that was swallowed,
        so the endpoint returned a 200 with an empty audio body.

        Resolution order:

        1. ``voice`` is a real voice name        -> use it.
        2. ``voice`` is a known preset id        -> use that preset
           (it wins over ``preset`` because the caller named it explicitly).
        3. ``preset`` is a known preset id       -> use it.
        4. otherwise                             -> service default voice.

        Returns:
            ``(voice_name, preset_cfg)`` where ``preset_cfg`` may be empty.
        """
        # 1. A genuine voice name was supplied.
        if self.is_valid_voice_name(voice):
            return voice.strip(), (self.resolve_preset(preset) or {})  # type: ignore[union-attr]

        # 2. The `voice` field actually carried a preset id.
        if voice:
            voice_as_preset = self.resolve_preset(voice)
            if voice_as_preset:
                return voice_as_preset["voice"], voice_as_preset
            logger.warning(
                "Unknown voice/preset '%s' supplied; falling back to preset/default", voice
            )

        # 3. Fall back to the explicit preset argument.
        preset_cfg = self.resolve_preset(preset)
        if preset_cfg:
            return preset_cfg["voice"], preset_cfg

        # 4. Service default.
        return self._voice, {}

    @property
    def available(self) -> bool:
        """Check if TTS is available (enabled in config AND at least one engine works)."""
        if not getattr(settings, "TTS_ENABLED", False):
            return False
        # When engine is edge-tts, require the package to be installed
        if self._engine == "edge" and not EDGE_TTS_AVAILABLE:
            return False
        return True

    @property
    def engine_available(self) -> bool:
        """Whether the underlying edge-tts library is installed."""
        return EDGE_TTS_AVAILABLE

    # -----------------------------------------------------------------
    # Preset helpers
    # -----------------------------------------------------------------

    @staticmethod
    def resolve_preset(preset: str | None) -> dict[str, Any] | None:
        """Return the preset dict for a given preset name, or None."""
        if not preset:
            return None
        return VOICE_PRESET_MAP.get(preset)

    @staticmethod
    def list_presets() -> list[dict[str, Any]]:
        """Return available voice presets."""
        return [
            {"id": key, **value}
            for key, value in VOICE_PRESET_MAP.items()
        ]

    # -----------------------------------------------------------------
    # SSML helpers
    # -----------------------------------------------------------------

    @staticmethod
    def build_ssml(
        text: str,
        voice: str,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
        style: str | None = None,
        breaks: bool = True,
    ) -> str:
        """Build SSML markup for edge-tts / Azure TTS.

        Args:
            text: Plain text to speak.
            voice: Voice name (e.g. zh-CN-XiaoxiaoNeural).
            rate: Speech rate (e.g. "+20%", "-10%").
            pitch: Pitch offset (e.g. "+5Hz", "-10%").
            volume: Volume adjustment (e.g. "+50%").
            style: Speaking style for Neural voices (e.g. "chat", "serious", "friendly").
            breaks: Whether to insert prosodic breaks at sentence boundaries.
        """
        escaped_text = html.escape(text, quote=True)

        # Insert short breaks after sentence-ending punctuation for more natural rhythm
        if breaks:
            for punct in ("。", "！", "？", "；", ".", "!", "?", ";"):
                escaped_text = escaped_text.replace(punct, f"{punct}<break time='300ms'/>")

        # Build the <voice> element, optionally with mstts:express-as for style
        voice_attrs = f'name="{voice}"'
        if style:
            voice_attrs += f' xmlns:mstts="http://www.w3.org/2001/mstts"'
            inner = (
                f'<mstts:express-as style="{html.escape(style, quote=True)}">'
                f"{escaped_text}"
                f"</mstts:express-as>"
            )
        else:
            inner = escaped_text

        return (
            f'<speak version="1.0" xml:lang="zh-CN" '
            f'xmlns="http://www.w3.org/2001/10/synthesis">\n'
            f'  <voice {voice_attrs}>\n'
            f'    <prosody rate="{html.escape(rate, quote=True)}" '
            f'pitch="{html.escape(pitch, quote=True)}" '
            f'volume="{html.escape(volume, quote=True)}">\n'
            f'      {inner}\n'
            f'    </prosody>\n'
            f'  </voice>\n'
            f'</speak>'
        )

    # -----------------------------------------------------------------
    # Primary synthesis entry points
    # -----------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        preset: str | None = None,
        use_ssml: bool = False,
        style: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize text to audio.

        Args:
            text: Text to synthesize (max 5000 chars).
            voice: Voice name override (ignored if preset is given).
            rate: Speech rate adjustment (e.g., "+20%", "-10%").
            volume: Volume adjustment (e.g., "+50%").
            pitch: Pitch offset (e.g. "+5Hz").
            preset: Voice preset name (formal_female, formal_male, warm_female).
            use_ssml: Whether to wrap the text in SSML for better prosody.
            style: Speaking style for Neural voices (chat, serious, friendly, etc).

        Returns:
            Dict with audio_content (bytes), format, duration_estimate, etc.
        """
        if not text or not text.strip():
            return {"error": "Empty text", "audio_content": b""}

        # Resolve voice / preset (accepts preset ids in either field).
        voice, preset_cfg = self.resolve_voice(voice, preset)
        if preset_cfg:
            rate = preset_cfg.get("rate", rate)
            pitch = preset_cfg.get("pitch", pitch)

        # Truncate long text
        if len(text) > 5000:
            text = text[:5000] + "......（语音输出已截断）"

        if self._engine == "azure":
            return await self._azure_tts(
                text, voice, rate, volume, pitch, style=style,
            )
        else:
            return await self._edge_tts(
                text, voice, rate, volume, pitch,
                use_ssml=use_ssml, style=style,
            )

    async def synthesize_stream(
        self,
        text: str,
        voice: str | None = None,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        preset: str | None = None,
        use_ssml: bool = False,
        style: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Stream synthesized audio in chunks.

        Yields audio chunks (MP3 bytes) as they become available.
        Returns silently if edge-tts is not installed.
        """
        if not text or not text.strip():
            return

        # Resolve voice / preset (accepts preset ids in either field).
        voice, preset_cfg = self.resolve_voice(voice, preset)
        if preset_cfg:
            rate = preset_cfg.get("rate", rate)
            pitch = preset_cfg.get("pitch", pitch)

        if len(text) > 5000:
            text = text[:5000]

        if not EDGE_TTS_AVAILABLE:
            logger.warning("edge-tts not installed, TTS streaming unavailable")
            return

        try:
            if use_ssml:
                ssml = self.build_ssml(text, voice, rate=rate, pitch=pitch,
                                       volume=volume, style=style)
                communicate = _edge_tts_module.Communicate(ssml, voice)
            else:
                communicate = _edge_tts_module.Communicate(
                    text, voice, rate=rate, volume=volume, pitch=pitch,
                )

            produced_any = False
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    produced_any = True
                    yield chunk["data"]

            if not produced_any:
                logger.error(
                    "TTS produced no audio for voice=%s (engine=%s); "
                    "check network egress to the edge-tts endpoint",
                    voice,
                    self._engine,
                )
        except Exception as exc:
            logger.error("TTS streaming failed (voice=%s): %s", voice, exc)
            return

    async def synthesize_stream_sse(
        self,
        text: str,
        **kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """Wrap synthesize_stream as a binary SSE-style stream.

        Yields raw audio chunks. The caller (endpoint) is responsible for
        setting the correct Content-Type and SSE framing.
        """
        async for chunk in self.synthesize_stream(text, **kwargs):
            yield chunk

    # -----------------------------------------------------------------
    # Engine: Edge TTS
    # -----------------------------------------------------------------

    async def _edge_tts(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str,
        pitch: str = "+0Hz",
        use_ssml: bool = False,
        style: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize using Microsoft Edge TTS (free, no API key)."""
        if not EDGE_TTS_AVAILABLE:
            return {
                "error": "edge-tts is not installed. Install with: pip install edge-tts",
                "audio_content": b"",
                "engine": "unavailable",
            }

        try:
            if use_ssml:
                ssml = self.build_ssml(text, voice, rate=rate, pitch=pitch,
                                       volume=volume, style=style)
                communicate = _edge_tts_module.Communicate(ssml, voice)
            else:
                communicate = _edge_tts_module.Communicate(
                    text, voice, rate=rate, volume=volume, pitch=pitch,
                )

            audio_buffer = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_buffer.write(chunk["data"])

            audio_data = audio_buffer.getvalue()

            # Estimate duration: MP3 at ~48kbps for speech
            duration_estimate = len(audio_data) / (48000 / 8)

            return {
                "audio_content": audio_data,
                "format": "mp3",
                "duration_estimate": round(duration_estimate, 1),
                "engine": "edge-tts",
                "voice": voice,
                "text_length": len(text),
                "ssml_used": use_ssml,
            }

        except Exception as exc:
            logger.error("Edge TTS failed: %s", exc)
            return {"error": str(exc), "audio_content": b""}

    # -----------------------------------------------------------------
    # Engine: Azure Cognitive Services TTS
    # -----------------------------------------------------------------

    async def _azure_tts(
        self,
        text: str,
        voice: str,
        rate: str,
        volume: str,
        pitch: str = "+0Hz",
        style: str | None = None,
    ) -> dict[str, Any]:
        """Synthesize using Azure Cognitive Services TTS."""
        api_key = os.getenv("AZURE_SPEECH_KEY", "")
        region = os.getenv("AZURE_SPEECH_REGION", "eastasia")

        if not api_key:
            # Fallback to edge
            logger.warning("Azure Speech key not configured, falling back to edge-tts")
            return await self._edge_tts(text, voice, rate, volume, pitch, use_ssml=True, style=style)

        import httpx

        # Azure TTS REST API
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-160kbitrate-mono-mp3",
        }

        # Build SSML using our helper (Azure always uses SSML)
        ssml = self.build_ssml(text, voice, rate=rate, pitch=pitch,
                               volume=volume, style=style)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, content=ssml.encode("utf-8"))
                resp.raise_for_status()

            audio_data = resp.content
            duration_estimate = len(audio_data) / (48000 / 8)

            return {
                "audio_content": audio_data,
                "format": "mp3",
                "duration_estimate": round(duration_estimate, 1),
                "engine": "azure",
                "voice": voice,
                "text_length": len(text),
                "ssml_used": True,
            }

        except Exception as exc:
            logger.error("Azure TTS failed: %s", exc)
            return await self._edge_tts(text, voice, rate, volume, pitch, use_ssml=True, style=style)

    # -----------------------------------------------------------------
    # Voice catalogue
    # -----------------------------------------------------------------

    def list_voices(self) -> list[dict[str, str]]:
        """List available Chinese TTS voices."""
        return [
            {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓（女声，温暖）", "gender": "female", "style": "温暖亲切"},
            {"id": "zh-CN-YunxiNeural", "name": "云希（男声，专业）", "gender": "male", "style": "专业沉稳"},
            {"id": "zh-CN-YunjianNeural", "name": "云健（男声，有力）", "gender": "male", "style": "铿锵有力"},
            {"id": "zh-CN-XiaoyiNeural", "name": "晓伊（女声，活泼）", "gender": "female", "style": "活泼自然"},
            {"id": "zh-CN-YunyangNeural", "name": "云扬（男声，新闻）", "gender": "male", "style": "新闻播报"},
            {"id": "zh-CN-XiaochenNeural", "name": "晓辰（女声，轻松）", "gender": "female", "style": "轻松随和"},
            {"id": "zh-CN-XiaohanNeural", "name": "晓涵（女声，优雅）", "gender": "female", "style": "优雅知性"},
            {"id": "zh-CN-XiaomengNeural", "name": "晓梦（女声，可爱）", "gender": "female", "style": "甜美可爱"},
            {"id": "zh-CN-XiaomoNeural", "name": "晓墨（女声，沉稳）", "gender": "female", "style": "沉稳大方"},
            {"id": "zh-CN-XiaoqiuNeural", "name": "晓秋（女声，知性）", "gender": "female", "style": "知性成熟"},
            {"id": "zh-CN-XiaoruiNeural", "name": "晓睿（女声，严肃）", "gender": "female", "style": "严肃正式"},
            {"id": "zh-CN-XiaoshuangNeural", "name": "晓双（女声，童声）", "gender": "female", "style": "儿童声"},
            {"id": "zh-CN-XiaoxuanNeural", "name": "晓萱（女声，亲切）", "gender": "female", "style": "亲切温和"},
            {"id": "zh-CN-XiaoyanNeural", "name": "晓颜（女声，平淡）", "gender": "female", "style": "平和自然"},
            {"id": "zh-CN-XiaozhenNeural", "name": "晓甄（女声，成熟）", "gender": "female", "style": "成熟稳重"},
            {"id": "zh-CN-YunfengNeural", "name": "云枫（男声，老年）", "gender": "male", "style": "老年男性"},
            {"id": "zh-CN-YunhaoNeural", "name": "云皓（男声，广告）", "gender": "male", "style": "广告配音"},
            {"id": "zh-CN-YunxiaNeural", "name": "云夏（男声，少年）", "gender": "male", "style": "少年声"},
            {"id": "zh-CN-YunyeNeural", "name": "云野（男声，叙事）", "gender": "male", "style": "纪录片旁白"},
            {"id": "zh-CN-YunzeNeural", "name": "云泽（男声，长者）", "gender": "male", "style": "长者风范"},
        ]


# =============================================================================
# Singleton
# =============================================================================

_tts_service: TTSService | None = None


def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
