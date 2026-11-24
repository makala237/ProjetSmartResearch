"""Per-request UI language context (derived from Accept-Language header)."""

from contextvars import ContextVar

_request_lang: ContextVar[str] = ContextVar("request_lang", default="fr")


def set_request_lang(accept_language: str | None) -> str:
    raw = (accept_language or "").strip().lower()
    lang = "en" if raw.startswith("en") else "fr"
    _request_lang.set(lang)
    return lang


def get_request_lang() -> str:
    return _request_lang.get() or "fr"


def reset_request_lang() -> None:
    _request_lang.set("fr")


def bi(fr: str, en: str) -> str:
    """Return English or French message according to the active request's Accept-Language."""
    return en if get_request_lang() == "en" else fr
