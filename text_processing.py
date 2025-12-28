"""Text post-processing helpers for dictation output."""

from __future__ import annotations

import re

SUFFIX_NONE = "none"
SUFFIX_SPACE = "space"
SUFFIX_NEWLINE = "newline"


def apply_punctuation_options(
    text: str,
    *,
    normalize_spaces: bool = False,
    capitalize: bool = False,
    terminal_punct: bool = False,
) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    cleaned = re.sub(r"[ \t]+(\r?\n)", r"\1", cleaned)

    if normalize_spaces:
        cleaned = " ".join(cleaned.split())

    if cleaned and capitalize:
        cleaned = cleaned[0].upper() + cleaned[1:]

    if cleaned and terminal_punct and cleaned[-1] not in ".!?":
        cleaned += "."

    return cleaned


def prepare_clipboard_text(
    text: str,
    *,
    suffix_mode: str = SUFFIX_SPACE,
    normalize_spaces: bool = False,
    capitalize: bool = False,
    terminal_punct: bool = False,
) -> str:
    cleaned = apply_punctuation_options(
        text,
        normalize_spaces=normalize_spaces,
        capitalize=capitalize,
        terminal_punct=terminal_punct,
    )
    if not cleaned:
        return ""
    cleaned = cleaned.rstrip()

    if suffix_mode == SUFFIX_NEWLINE:
        return cleaned + "\n"
    if suffix_mode == SUFFIX_SPACE:
        return cleaned + " "
    return cleaned
