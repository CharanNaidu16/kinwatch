"""User-facing fixed copy in English, Hindi and Kannada.

Hindi/Kannada lines need a native-speaker check before filming (PLAN.md §16).
"""
from __future__ import annotations

from backend.schemas import Language

_TEXTS: dict[str, dict[Language, str]] = {
    "fallback_reply": {
        "en": "Sorry, please tap a button or reply yes/no.",
        "hi": "माफ़ कीजिए, कृपया बटन दबाएँ या हाँ/नहीं लिखें।",
        "kn": "ಕ್ಷಮಿಸಿ, ದಯವಿಟ್ಟು ಬಟನ್ ಒತ್ತಿ ಅಥವಾ ಹೌದು/ಇಲ್ಲ ಎಂದು ಉತ್ತರಿಸಿ.",
    },
    "unsupported": {
        "en": "I can't watch for anything about health. I can only tell you what the camera sees, "
              "for example if Papa gets up at night and doesn't come back to bed. Shall I set that up?",
        "hi": "मैं सेहत के बारे में नहीं बता सकता। मैं बस यह बता सकता हूँ कि कैमरे में क्या दिखता है — "
              "जैसे पापा रात को बिस्तर से उठें और वापस न आएँ। क्या ऐसा नियम बनाऊँ?",
        "kn": "ಆರೋಗ್ಯದ ಬಗ್ಗೆ ನಾನು ಹೇಳಲಾರೆ. ಕ್ಯಾಮೆರಾದಲ್ಲಿ ಏನು ಕಾಣುತ್ತದೆ ಎಂಬುದನ್ನು ಮಾತ್ರ ಹೇಳಬಲ್ಲೆ — "
              "ಉದಾಹರಣೆಗೆ ಅಪ್ಪ ರಾತ್ರಿ ಹಾಸಿಗೆಯಿಂದ ಎದ್ದು ವಾಪಸ್ ಬರದಿದ್ದರೆ. ಹಾಗೆ ಒಂದು ನಿಯಮ ಮಾಡಲೇ?",
    },
    "clarify_generic": {
        "en": "I didn't quite get that. What should I watch, and when should I tell you?",
        "hi": "मैं ठीक से समझ नहीं पाया। मुझे किस चीज़ पर नज़र रखनी है, और कब बताना है?",
        "kn": "ನನಗೆ ಸರಿಯಾಗಿ ಅರ್ಥವಾಗಲಿಲ್ಲ. ನಾನು ಯಾವುದರ ಮೇಲೆ ಗಮನ ಇಡಬೇಕು, ಯಾವಾಗ ತಿಳಿಸಬೇಕು?",
    },
    "clarify_one_object": {
        "en": "I can watch one object per rule. Which one should I watch: {objects}?",
        "hi": "एक नियम में मैं एक ही चीज़ पर नज़र रख सकता हूँ। किस पर रखूँ: {objects}?",
        "kn": "ಒಂದು ನಿಯಮದಲ್ಲಿ ಒಂದೇ ವಸ್ತುವಿನ ಮೇಲೆ ಗಮನ ಇಡಬಲ್ಲೆ. ಯಾವುದರ ಮೇಲೆ ಇಡಲಿ: {objects}?",
    },
    "clarify_unknown_name": {
        "en": "Who is {name}? I can alert: {known}.",
        "hi": "{name} कौन हैं? मैं इन्हें बता सकता हूँ: {known}।",
        "kn": "{name} ಯಾರು? ನಾನು ಇವರಿಗೆ ತಿಳಿಸಬಲ್ಲೆ: {known}.",
    },
    "heard": {
        "en": "I heard: \"{text}\"",
        "hi": "मैंने सुना: \"{text}\"",
        "kn": "ನಾನು ಕೇಳಿದ್ದು: \"{text}\"",
    },
    "voice_failed": {
        "en": "Sorry, I couldn't hear that voice note. Could you send it again, or type it?",
        "hi": "माफ़ कीजिए, मैं यह वॉइस नोट सुन नहीं पाया। फिर से भेजें या लिखकर भेजें।",
        "kn": "ಕ್ಷಮಿಸಿ, ಈ ವಾಯ್ಸ್ ನೋಟ್ ಕೇಳಿಸಲಿಲ್ಲ. ಮತ್ತೆ ಕಳುಹಿಸಿ ಅಥವಾ ಟೈಪ್ ಮಾಡಿ.",
    },
    "armed": {
        "en": "✅ Rule is on.",
        "hi": "✅ नियम चालू।",
        "kn": "✅ ನಿಯಮ ಚಾಲೂ ಆಗಿದೆ.",
    },
    "not_set": {
        "en": "Okay, I didn't set it. Tell me again what I should watch.",
        "hi": "ठीक है, मैंने यह नियम नहीं बनाया। फिर से बताइए कि किस पर नज़र रखूँ।",
        "kn": "ಸರಿ, ನಾನು ಈ ನಿಯಮ ಮಾಡಲಿಲ್ಲ. ಯಾವುದರ ಮೇಲೆ ಗಮನ ಇಡಬೇಕು ಎಂದು ಮತ್ತೆ ಹೇಳಿ.",
    },
    "error": {
        "en": "Sorry, something went wrong on my side. Please try again in a minute.",
        "hi": "माफ़ कीजिए, मेरी तरफ़ से कुछ गड़बड़ हो गई। एक मिनट बाद फिर कोशिश करें।",
        "kn": "ಕ್ಷಮಿಸಿ, ನನ್ನ ಕಡೆಯಿಂದ ಏನೋ ತಪ್ಪಾಗಿದೆ. ಒಂದು ನಿಮಿಷದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    },
    "button_yes": {"en": "Yes ✓", "hi": "हाँ ✓", "kn": "ಹೌದು ✓"},
    "button_no": {"en": "No ✗", "hi": "नहीं ✗", "kn": "ಇಲ್ಲ ✗"},
    "clarify_time_window": {
        "en": "The start and end time are the same. From what time to what time should I watch?",
        "hi": "शुरू और खत्म होने का समय एक ही है। किस समय से किस समय तक नज़र रखूँ?",
        "kn": "ಆರಂಭ ಮತ್ತು ಮುಕ್ತಾಯದ ಸಮಯ ಒಂದೇ ಆಗಿದೆ. ಯಾವ ಸಮಯದಿಂದ ಯಾವ ಸಮಯದವರೆಗೆ ಗಮನ ಇಡಲಿ?",
    },
}


def text(key: str, language: Language, **fmt: str) -> str:
    template = _TEXTS[key].get(language) or _TEXTS[key]["en"]
    return template.format(**fmt) if fmt else template


def keys() -> list[str]:
    return list(_TEXTS)
