"""Language detection + local translation providers.

No cloud translation APIs. The declared CSV language is never trusted blindly:
detection runs on the actual description text using two local detectors
(lingua + langdetect). Because both detectors are unreliable on very short
sentences, the declared language is used ONLY as a tie-breaker when the
detectors disagree and no high-confidence text evidence exists.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from functools import lru_cache

import httpx
from langdetect import DetectorFactory, LangDetectException, detect, detect_langs

from .. import config

# langdetect is randomised per run; pin the seed for reproducible ingestion.
DetectorFactory.seed = 0

ENGLISH = "en"

# Declared language name → ISO code used by the detectors.
DECLARED_TO_ISO: dict[str, str] = {
    "english": "en", "hindi": "hi", "romanian": "ro", "slovene": "sl",
    "serbian": "sr", "tamil": "ta", "dutch": "nl", "german": "de",
    "macedonian": "mk", "spanish": "es", "gujarati": "gu", "russian": "ru",
    "french": "fr", "italian": "it", "georgian": "ka", "polish": "pl",
    "chinese": "zh", "malayalam": "ml", "tagalog": "tl", "filipino": "tl",
    "portuguese": "pt", "norwegian": "no", "estonian": "et", "turkish": "tr",
    "arabic": "ar", "urdu": "ur", "hungarian": "hu", "indonesian": "id",
    "malay": "ms", "bulgarian": "bg", "danish": "da", "bosnian": "bs",
    "marathi": "mr", "swedish": "sv", "albanian": "sq",
}

HIGH_CONFIDENCE = 0.85


class TranslationError(Exception):
    pass


@lru_cache(maxsize=1)
def _lingua_detector():
    from lingua import LanguageDetectorBuilder

    return LanguageDetectorBuilder.from_all_languages().with_preloaded_language_models().build()


def _iso(lang) -> str:
    """Normalize lingua/langdetect language objects/codes to ISO-639-1-ish codes."""
    code = getattr(lang, "iso_code_639_1", None)
    if code is not None:
        return code.name.lower()
    if hasattr(lang, "name"):  # lingua Language enum
        try:
            return lang.iso_code_639_1.name.lower()
        except Exception:  # noqa: BLE001
            return lang.name.lower()
    return str(lang).lower()


def detect_language(text: str) -> str:
    """Primary text detection (kept for API compatibility)."""
    final, _, _, _ = decide_language(text, None)
    return final


def detect_language_batch(texts: list[str]) -> list[str]:
    return [detect_language(t) for t in texts]


def detect_all(text: str) -> dict:
    """Run all local detectors and return raw outputs."""
    text = (text or "").strip()
    if not text:
        return {"lingua": ("unknown", 0.0), "langdetect": ("unknown", 0.0),
                "langid": ("unknown", 0.0)}
    try:
        conf_values = _lingua_detector().compute_language_confidence_values(text)
        ling = (_iso(conf_values[0].language), float(conf_values[0].value)) if conf_values else ("unknown", 0.0)
    except Exception:  # noqa: BLE001
        ling = ("unknown", 0.0)
    try:
        top = detect_langs(text)[0]
        langdet = (str(top.lang), float(top.prob))
    except (LangDetectException, Exception):  # noqa: BLE001
        langdet = ("unknown", 0.0)
    try:
        import py3langid
        lid = (str(py3langid.classify(text)[0]), 1.0)
    except Exception:  # noqa: BLE001
        lid = ("unknown", 0.0)
    return {"lingua": ling, "langdetect": langdet, "langid": lid}


def _langid_batch(texts: list[str]) -> list[str]:
    """Fast batched py3langid classification (falls back to per-row)."""
    if not texts:
        return []
    try:
        import py3langid

        raw = py3langid.classify_batch(texts)
        out: list[str] = []
        for item in raw:
            if isinstance(item, tuple):
                out.append(str(item[0]))
            else:
                out.append(str(item))
        if len(out) == len(texts):
            return out
    except Exception:  # noqa: BLE001
        pass
    try:
        import py3langid

        return [str(py3langid.classify(t)[0]) for t in texts]
    except Exception:  # noqa: BLE001
        return ["unknown"] * len(texts)


def _decide(text: str, declared_language: str | None, lid_code: str) -> tuple[str, str, float, str]:
    """Text evidence first (3-detector majority), declared value only as tie-breaker.

    Rules:
      1. ≥2 of 3 detectors agree       → detector_majority
      2. best of lingua/langdetect ≥ HIGH_CONFIDENCE → detector_high_confidence
      3. declared maps to ISO          → declared_fallback (ambiguous text)
      4. otherwise                     → langid output
    """
    text = (text or "").strip()
    if not text:
        return "unknown", "unknown", 0.0, "empty"

    res = detect_all(text)
    ling, lconf = res["lingua"]
    det, dconf = res["langdetect"]
    lid = lid_code if lid_code != "unknown" else res["langid"][0]
    conf = max(lconf, dconf)

    votes: dict[str, int] = {}
    for code in (ling, det, lid):
        if code and code != "unknown":
            votes[code] = votes.get(code, 0) + 1
    majority = max(votes, key=lambda k: votes[k]) if votes else "unknown"
    raw = lid if lid != "unknown" else (ling if lconf >= dconf else det)

    if votes.get(majority, 0) >= 2:
        return majority, raw, conf, "detector_majority"
    if conf >= HIGH_CONFIDENCE:
        return raw, raw, conf, "detector_high_confidence"
    declared_iso = DECLARED_TO_ISO.get((declared_language or "").strip().lower())
    if declared_iso:
        return declared_iso, raw, conf, "declared_fallback"
    return raw, raw, conf, "detector_low_confidence"


def decide_language(text: str, declared_language: str | None) -> tuple[str, str, float, str]:
    return _decide(text, declared_language, "unknown")


def decide_language_batch(
    items: list[tuple[str, str | None]],
) -> list[tuple[str, str, float, str]]:
    """Batched variant used by the ingestion worker (fast path)."""
    lid_codes = _langid_batch([t for t, _ in items])
    return [_decide(text, declared, lid) for (text, declared), lid in zip(items, lid_codes)]


class TranslationProvider(ABC):
    """Provider abstraction — swap local engines without touching the pipeline."""

    name: str = "abstract"

    @abstractmethod
    def translate(self, text: str, source_language: str) -> str:
        """Return English translation or raise TranslationError."""


class NoopTranslationProvider(TranslationProvider):
    name = "none"

    def translate(self, text: str, source_language: str) -> str:
        raise TranslationError("translation provider disabled")


class OllamaTranslationProvider(TranslationProvider):
    """Local Ollama model (default qwen:latest) at localhost:11434 — no API key."""

    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or config.OLLAMA_URL).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout

    def translate(self, text: str, source_language: str) -> str:
        prompt = (
            "Translate the following text to English. "
            "Reply with the translation only, no quotes, no explanations.\n\n"
            f"{text}"
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 512},
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:  # noqa: BLE001 — surfaced as translation_status=FAILED
            raise TranslationError(f"ollama request failed: {exc}") from exc
        out = (body.get("response") or "").strip().strip('"')
        if not out:
            raise TranslationError("empty translation from provider")
        return out


def get_provider(name: str | None = None) -> TranslationProvider:
    chosen = (name or config.TRANSLATION_PROVIDER).lower()
    if chosen in ("none", "", "off", "disabled"):
        return NoopTranslationProvider()
    if chosen == "ollama":
        return OllamaTranslationProvider()
    raise ValueError(f"unknown translation provider: {chosen}")


def provider_health(name: str | None = None) -> dict:
    provider = get_provider(name)
    if isinstance(provider, OllamaTranslationProvider):
        try:
            r = httpx.get(f"{provider.base_url}/api/tags", timeout=5.0)
            models = [m.get("name") for m in r.json().get("models", [])] if r.is_success else []
            return {"provider": provider.name, "ok": r.is_success, "models": models}
        except Exception as exc:  # noqa: BLE001
            return {"provider": provider.name, "ok": False, "error": str(exc)}
    return {"provider": provider.name, "ok": True}


def translate_to_english(text: str, source_language: str, provider: TranslationProvider) -> str:
    """Single entry point used by the translation stage."""
    result = provider.translate(text, source_language)
    if not result or not result.strip():
        raise TranslationError("empty translation")
    return result.strip()


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
