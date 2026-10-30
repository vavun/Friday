import re

_SENTENCE_SPLIT_RE = re.compile(
    r'(?<=[.!?])\s+(?=[A-Z0-9"\'(])|\n+'
)


def sanitize_for_tts(text: str) -> str:
    """Normalize text for natural spoken speech."""
    if not text:
        return ""

    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"https?://(?:www\.)?([a-zA-Z0-9.-]+)(?:/[^\s]*)?", r"\1", text)
    text = re.sub(r"^[#>\-\*\+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]{1,3}([^*_~]+)[*_~]{1,3}", r"\1", text)
    text = re.sub(r"\$(\d+(?:\.\d+)?)", r"\1 dollars", text)
    text = re.sub(r"€(\d+(?:\.\d+)?)", r"\1 euros", text)
    text = re.sub(r"£(\d+(?:\.\d+)?)", r"\1 pounds", text)
    text = re.sub(r"\s*&\s*", " and ", text)
    text = re.sub(r"\s*%\s*", " percent ", text)
    text = re.sub(r"\s*@\s*", " at ", text)
    text = re.sub(r"\be\.g\.(?:\b|$)", "for example", text, flags=re.IGNORECASE)
    text = re.sub(r"\bi\.e\.(?:\b|$)", "that is", text, flags=re.IGNORECASE)
    text = re.sub(r"\bapprox(?:\.|\b)", "approximately", text, flags=re.IGNORECASE)
    text = re.sub(r"\bvs(?:\.|\b)", "versus", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s.,!?:;\'\"()\-–—«»]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_sentences(text: str, min_chars: int = 6) -> list[str]:
    """Split text into sentence chunks for streaming synthesis."""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    merged: list[str] = []
    carry = ""
    for part in parts:
        candidate = f"{carry} {part}".strip() if carry else part
        if len(candidate) < min_chars:
            carry = candidate
            continue
        merged.append(candidate)
        carry = ""
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}".strip()
        else:
            merged.append(carry)
    return merged
