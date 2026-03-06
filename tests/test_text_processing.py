from text_processing import (
    SUFFIX_NEWLINE,
    SUFFIX_NONE,
    SUFFIX_SPACE,
    apply_punctuation_options,
    prepare_clipboard_text,
)


def test_apply_punctuation_options_normalize_capitalize_terminal():
    text = "  hello   world  "
    result = apply_punctuation_options(
        text,
        normalize_spaces=True,
        capitalize=True,
        terminal_punct=True,
    )
    assert result == "Hello world."


def test_apply_punctuation_options_trims_spaces_before_newline():
    text = "First sentence. \nSecond sentence."
    result = apply_punctuation_options(text)
    assert result == "First sentence.\nSecond sentence."


def test_apply_punctuation_options_keeps_terminal_punct():
    text = "Already!"
    result = apply_punctuation_options(text, terminal_punct=True)
    assert result == "Already!"


def test_apply_punctuation_options_empty_input():
    assert apply_punctuation_options("") == ""


def test_prepare_clipboard_text_suffix_space_only_at_end():
    text = "First sentence. \nSecond sentence."
    result = prepare_clipboard_text(text, suffix_mode=SUFFIX_SPACE)
    assert result == "First sentence.\nSecond sentence. "


def test_prepare_clipboard_text_suffix_newline():
    result = prepare_clipboard_text("Hello", suffix_mode=SUFFIX_NEWLINE)
    assert result == "Hello\n"


def test_prepare_clipboard_text_suffix_none():
    result = prepare_clipboard_text("Hello", suffix_mode=SUFFIX_NONE)
    assert result == "Hello"


def test_prepare_clipboard_text_empty_input():
    result = prepare_clipboard_text("   ", suffix_mode=SUFFIX_SPACE)
    assert result == ""
