"""Language names used in translation and terminology prompts."""

NAMES = {
    "en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French",
    "de": "German", "es": "Spanish", "ru": "Russian", "ar": "Arabic",
    **dict.fromkeys(("zh", "zh-cn", "zh-sg", "zh-hans"), "Simplified Chinese"),
    **dict.fromkeys(("zh-tw", "zh-hk", "zh-mo", "zh-hant"), "Traditional Chinese"),
}


def language_name(code):
    return NAMES.get(code.lower().replace("_", "-"), code)


def translation_direction(source, target):
    target_name = language_name(target)
    direction = f"from {language_name(source)} to {target_name}."
    if target_name == "Simplified Chinese":
        return direction + " Use Simplified Chinese characters (简体字) consistently throughout the translation."
    if target_name == "Traditional Chinese":
        return direction + " Use Traditional Chinese characters (繁體字) consistently throughout the translation."
    return direction
