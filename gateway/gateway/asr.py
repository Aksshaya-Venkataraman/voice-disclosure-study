"""Speech recognition with Google Cloud Speech-to-Text.

Audio is transcribed in memory and never written to disk or the database.
Credentials come from GOOGLE_APPLICATION_CREDENTIALS (a mounted secret file).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

# content type -> (encoding name, sample rate or None to read it from the file header)
FORMATS: dict[str, tuple[str, int | None]] = {
    "audio/webm": ("WEBM_OPUS", 48000),
    "audio/ogg": ("OGG_OPUS", 48000),
    "audio/wav": ("LINEAR16", None),
    "audio/x-wav": ("LINEAR16", None),
    "audio/wave": ("LINEAR16", None),
}


class ASRError(RuntimeError):
    """Speech recognition failed (network, quota, credentials, bad audio)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes, content_type: str) -> Transcript: ...


def base_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


class GoogleSpeechToText:
    def __init__(self, language: str, client: Any | None = None) -> None:
        from google.cloud import speech  # imported lazily: only needed when enabled

        self._speech = speech
        self._client = client or speech.SpeechClient()
        self._language = language

    def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        fmt = FORMATS.get(base_content_type(content_type))
        if fmt is None:
            raise ASRError(f"unsupported audio format {content_type!r}")
        encoding, sample_rate = fmt
        speech = self._speech
        config_kwargs: dict[str, Any] = {
            "encoding": getattr(speech.RecognitionConfig.AudioEncoding, encoding),
            "language_code": self._language,
            "enable_automatic_punctuation": True,
            "model": "latest_long",
        }
        if sample_rate:
            config_kwargs["sample_rate_hertz"] = sample_rate
        config = speech.RecognitionConfig(**config_kwargs)
        recording = speech.RecognitionAudio(content=audio)

        from google.api_core import exceptions as gexc

        try:
            response = self._client.recognize(config=config, audio=recording, timeout=60)
        except gexc.InvalidArgument as exc:
            if "too long" not in str(exc).lower() and "duration" not in str(exc).lower():
                raise ASRError("speech recognition rejected the audio") from exc
            # Synchronous recognition is limited to about a minute of audio.
            try:
                response = self._client.long_running_recognize(config=config, audio=recording).result(timeout=240)
            except gexc.GoogleAPICallError as long_exc:
                raise ASRError("speech recognition failed") from long_exc
        except gexc.GoogleAPICallError as exc:
            log.warning("speech recognition failed: %s", type(exc).__name__)
            raise ASRError("speech recognition failed") from exc

        parts, confidences = [], []
        for result in response.results:
            if result.alternatives:
                best = result.alternatives[0]
                parts.append(best.transcript.strip())
                confidences.append(best.confidence)
        text = " ".join(p for p in parts if p)
        confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
        return Transcript(text=text, confidence=confidence)
