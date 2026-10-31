"""Regression tests for selected-language reply-keyboard styling."""
from localization import LANGUAGES, language_keyboard


def test_saved_language_is_the_only_success_styled_button():
    for selected in LANGUAGES:
        keyboard = language_keyboard(selected)
        buttons = {
            button.text: button
            for row in keyboard.keyboard
            for button in row
        }

        assert len(buttons) == len(LANGUAGES)
        for code, details in LANGUAGES.items():
            label = f"{details['flag']} {details['name']}"
            expected = "success" if code == selected else "primary"
            assert buttons[label].style == expected


def test_unknown_language_falls_back_to_english_highlight():
    keyboard = language_keyboard("unsupported")
    buttons = [
        button
        for row in keyboard.keyboard
        for button in row
    ]
    english = next(button for button in buttons if button.text == "🇬🇧 English")

    assert english.style == "success"
    assert all(
        button.style == "primary"
        for button in buttons
        if button is not english
    )
