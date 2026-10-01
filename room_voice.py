"""Dictation: record the default microphone, then transcribe it with OpenAI speech-to-text."""
from __future__ import annotations

import io
import threading
import time
import wave

import requests

from providers import DEFAULT_TIMEOUT, _chat_error_text, _request, resolve_credentials

TRANSCRIBE_MODEL = 'gpt-4o-mini-transcribe'
MAX_SECONDS = 180  # A 48 kHz mono recording stays under the 25 MB upload limit.
MIN_SECONDS = 0.4


class VoiceError(Exception):
    pass


class Recorder:
    """16-bit mono capture from the default input device. Raw bytes; numpy is not needed."""

    def __init__(self):
        self.chunks: list[bytes] = []
        self.lock = threading.Lock()
        self.stream = None
        self.rate = 16000
        self.started = 0.0

    def start(self):
        try:
            import sounddevice as sd  # Imported on first use so the app still opens without audio support.
            self.rate = int(sd.query_devices(kind='input')['default_samplerate']) or 16000

            def collect(data, frames, time_info, status):
                with self.lock:
                    self.chunks.append(bytes(data))

            self.stream = sd.RawInputStream(samplerate=self.rate, channels=1, dtype='int16', callback=collect)
            self.stream.start()
        except Exception as exc:
            raise VoiceError(f'Microphone unavailable ({type(exc).__name__}). '
                             'Check Windows microphone privacy settings, or press Win+H for Windows voice typing.') from None
        self.started = time.monotonic()

    @property
    def elapsed(self):
        return time.monotonic() - self.started if self.started else 0.0

    def stop(self) -> bytes:
        """Stop recording and return a WAV file."""
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
        with self.lock:
            pcm = b''.join(self.chunks)
        return to_wav(pcm, self.rate)


def to_wav(pcm: bytes, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), 'rb') as source:
        return source.getnframes() / float(source.getframerate() or 1)


def transcribe(data: bytes, cfg) -> str:
    key, base, _ = resolve_credentials('openai', cfg)
    if not key:
        raise VoiceError('Voice input uses your OpenAI API key. Add it in Settings & APIs, '
                         'or press Win+H for Windows voice typing.')
    try:
        resp = _request('POST', base.rstrip('/') + '/audio/transcriptions',
                        headers={'Authorization': f'Bearer {key.strip()}'},
                        files={'file': ('speech.wav', data, 'audio/wav')},
                        data={'model': TRANSCRIBE_MODEL, 'response_format': 'json'},
                        timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise VoiceError(f'Transcription request failed ({type(exc).__name__}).') from None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400:
        error = body.get('error') if isinstance(body, dict) else None
        message = error.get('message') if isinstance(error, dict) else resp.text
        raise VoiceError(f'Transcription failed: HTTP {resp.status_code}: {_chat_error_text(message, key)}')
    text = body.get('text') if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise VoiceError('No speech was recognised. Try again a little closer to the microphone.')
    return text.strip()
