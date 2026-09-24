"""Voice input service for speech-to-text.

Supports:
- WAV, MP3, M4A, WebM audio files
- Chinese language recognition
- Uses Whisper (openai-whisper) or fallback to Vosk

The speech recognition model is lazy-loaded so the system works without
voice libraries installed, with graceful degradation.
"""

import io
import logging
import os
import tempfile
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Simplified-Chinese prompt biases Whisper toward Simplified output (Whisper's
# base model otherwise tends to emit Traditional Chinese for zh audio).
_SIMPLIFIED_PROMPT = "以下是普通话对话，请用简体中文输出。"

# Safe full-width conversion for punctuation marks that Whisper emits as ASCII.
# Commas/periods between digits are preserved (they are decimal/thousand
# separators, not sentence punctuation).
_PUNCT_MAP = {
    "?": "？",
    "!": "！",
    ";": "；",
    ":": "：",
}

# Lazy-loaded OpenCC converter (Traditional -> Simplified); None if unavailable.
_opencc_converter: Any = None
_opencc_tried = False


def _get_opencc_converter() -> Any:
    """Return an OpenCC t2s converter, or None if opencc is not installed."""
    global _opencc_converter, _opencc_tried
    if _opencc_tried:
        return _opencc_converter
    _opencc_tried = True
    try:
        from opencc import OpenCC  # type: ignore
        _opencc_converter = OpenCC("t2s")
        logger.info("OpenCC (t2s) loaded for Traditional->Simplified normalization")
    except Exception as exc:
        logger.warning("OpenCC not available, skipping 繁->简 normalization: %s", exc)
        _opencc_converter = None
    return _opencc_converter


class VoiceService:
    """Voice input service for speech-to-text.

    Supports:
    - WAV, MP3, M4A, WebM audio files
    - Chinese language recognition
    - Uses Whisper (openai-whisper) or fallback to Vosk

    The model is lazy-loaded on first use.  If no speech recognition
    library is installed, the service reports itself as unavailable.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._engine_type: str = ""

    # ------------------------------------------------------------------
    # Model initialisation
    # ------------------------------------------------------------------

    def _init_model(self) -> None:
        """Lazy-load speech recognition model."""
        if self._model is not None:
            return

        try:
            import whisper
            model_size = settings.VOICE_MODEL_SIZE
            model_dir = getattr(settings, "WHISPER_MODEL_DIR", None)
            if model_dir:
                os.makedirs(model_dir, exist_ok=True)
                self._model = whisper.load_model(model_size, download_root=model_dir)
            else:
                self._model = whisper.load_model(model_size)
            self._engine_type = "whisper"
            logger.info("Voice engine: Whisper (model=%s)", model_size)
            return
        except ImportError:
            logger.warning("openai-whisper not installed, voice input disabled")
        except Exception as exc:
            logger.warning("Failed to load Whisper model: %s", exc)

        # No fallback engine available for now
        logger.warning("No speech recognition engine available")

    def _ensure_model(self) -> None:
        """Ensure the speech recognition model is loaded."""
        if self._model is None:
            self._init_model()
        if self._model is None:
            raise RuntimeError(
                "Voice input is not available. Install optional dependency: "
                "openai-whisper (requires ffmpeg)."
            )

    @property
    def available(self) -> bool:
        """Return True if the speech recognition model can be loaded."""
        try:
            self._ensure_model()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def transcribe(self, audio_content: bytes, filename: str) -> dict[str, Any]:
        """Transcribe audio to text.

        Args:
            audio_content: Raw audio file bytes.
            filename: Original filename (used to determine format).

        Returns:
            {
                'text': transcribed text,
                'language': 'zh',
                'duration_seconds': 30.5,
                'confidence': 0.95
            }
        """
        self._ensure_model()

        ext = os.path.splitext(filename)[1].lower()

        # Whisper requires the audio on disk (or a file-like object with a
        # recognizable name).  Write to a temp file with the correct extension.
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_content)
            tmp_path = tmp.name

        try:
            result = self._model.transcribe(
                tmp_path,
                language="zh",
                task="transcribe",
                initial_prompt=_SIMPLIFIED_PROMPT,
            )
        finally:
            os.unlink(tmp_path)

        text = result.get("text", "").strip()
        text = self._normalize_simplified(text)
        text = self._normalize_punctuation(text)
        language = result.get("language", "zh")
        duration = self._estimate_duration(audio_content, ext)

        # Whisper doesn't provide a single confidence score; use the average
        # no-speech probability as a rough proxy.
        segments = result.get("segments", [])
        if segments:
            avg_no_speech = sum(s.get("no_speech_prob", 0.0) for s in segments) / len(segments)
            confidence = round(1.0 - avg_no_speech, 3)
        else:
            confidence = 0.0

        return {
            "text": text,
            "language": language,
            "duration_seconds": duration,
            "confidence": confidence,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_simplified(text: str) -> str:
        """Convert any Traditional Chinese output to Simplified Chinese."""
        if not text:
            return text
        converter = _get_opencc_converter()
        if converter is None:
            return text
        try:
            return converter.convert(text)
        except Exception as exc:
            logger.warning("繁->简 conversion failed: %s", exc)
            return text

    @staticmethod
    def _normalize_punctuation(text: str) -> str:
        """Convert ASCII punctuation to full-width CJK punctuation.

        Commas and periods sitting between two digits are left untouched so
        decimals (3.5) and thousand separators (1,000) are not corrupted.
        """
        if not text:
            return text
        out: list[str] = []
        n = len(text)
        for i, ch in enumerate(text):
            prev_digit = i > 0 and text[i - 1].isdigit()
            next_digit = i < n - 1 and text[i + 1].isdigit()
            if ch == ",":
                out.append("," if (prev_digit and next_digit) else "，")
            elif ch == ".":
                out.append("." if (prev_digit and next_digit) else "。")
            elif ch in _PUNCT_MAP:
                out.append(_PUNCT_MAP[ch])
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _estimate_duration(audio_content: bytes, ext: str) -> float:
        """Estimate audio duration in seconds from raw bytes.

        For WAV files this is accurate (header contains sample rate / channels).
        For compressed formats it's a rough estimate based on file size and
        typical bitrates.
        """
        if ext == ".wav":
            # WAV header: 44 bytes minimum; bytes 28-31 = byte rate
            if len(audio_content) > 44:
                import struct
                try:
                    byte_rate = struct.unpack("<I", audio_content[28:32])[0]
                    if byte_rate > 0:
                        return round((len(audio_content) - 44) / byte_rate, 2)
                except Exception:
                    pass

        # Rough estimate for compressed audio: assume ~16 kbps (typical for
        # voice recordings)
        estimated_bitrate = 16_000 / 8  # 16 kbps in bytes/sec
        return round(len(audio_content) / estimated_bitrate, 2)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_voice_service: VoiceService | None = None


def get_voice_service() -> VoiceService:
    """Return a singleton VoiceService instance."""
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()
    return _voice_service
