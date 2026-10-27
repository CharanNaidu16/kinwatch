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
    "introduce": {
        "en": "Hi! I'm Kinwatch. I don't know you yet. Your Telegram ID is {address}. "
              "Ask your family's Kinwatch admin to add it, then message me again.",
        "hi": "नमस्ते! मैं Kinwatch हूँ। मैं आपको अभी नहीं जानता। आपकी Telegram ID {address} है। "
              "परिवार के Kinwatch एडमिन से इसे जुड़वाएँ, फिर मुझे दोबारा मैसेज करें।",
        "kn": "ನಮಸ್ಕಾರ! ನಾನು Kinwatch. ನಿಮ್ಮ ಪರಿಚಯ ನನಗೆ ಇನ್ನೂ ಇಲ್ಲ. ನಿಮ್ಮ Telegram ID {address}. "
              "ಕುಟುಂಬದ Kinwatch ಅಡ್ಮಿನ್‌ಗೆ ಇದನ್ನು ಸೇರಿಸಲು ಹೇಳಿ, ನಂತರ ಮತ್ತೆ ಮೆಸೇಜ್ ಮಾಡಿ.",
    },
    "camera_offline": {
        "en": "The camera isn't sending pictures right now. Open the camera link on the camera phone, "
              "then send your message again.",
        "hi": "कैमरा अभी तस्वीरें नहीं भेज रहा। कैमरा फ़ोन पर कैमरा लिंक खोलें, फिर अपना मैसेज दोबारा भेजें।",
        "kn": "ಕ್ಯಾಮೆರಾ ಈಗ ಚಿತ್ರಗಳನ್ನು ಕಳುಹಿಸುತ್ತಿಲ್ಲ. ಕ್ಯಾಮೆರಾ ಫೋನ್‌ನಲ್ಲಿ ಕ್ಯಾಮೆರಾ ಲಿಂಕ್ ತೆರೆಯಿರಿ, "
              "ನಂತರ ನಿಮ್ಮ ಮೆಸೇಜ್ ಮತ್ತೆ ಕಳುಹಿಸಿ.",
    },
    "zone_not_found": {
        "en": "I can't see a {zone} in the camera view. Point the phone at it and send your message again.",
        "hi": "मुझे कैमरे में {zone} नहीं दिख रहा। फ़ोन को उसकी तरफ़ करें और मैसेज दोबारा भेजें।",
        "kn": "ಕ್ಯಾಮೆರಾದಲ್ಲಿ {zone} ಕಾಣುತ್ತಿಲ್ಲ. ಫೋನ್ ಅನ್ನು ಅದರ ಕಡೆ ತಿರುಗಿಸಿ ಮೆಸೇಜ್ ಮತ್ತೆ ಕಳುಹಿಸಿ.",
    },
    "pick_one": {
        "en": "Which one should I watch? Reply with its number.",
        "hi": "किस पर नज़र रखूँ? उसका नंबर लिखें।",
        "kn": "ಯಾವುದರ ಮೇಲೆ ಗಮನ ಇಡಲಿ? ಅದರ ಸಂಖ್ಯೆ ಕಳುಹಿಸಿ.",
    },
    "claim_button": {"en": "I've got it", "hi": "मैं देख लूँगा", "kn": "ನಾನು ನೋಡ್ತೀನಿ"},
    "claim_thanks": {
        "en": "Thanks, I've told the others.",
        "hi": "धन्यवाद, मैंने बाकी लोगों को बता दिया है।",
        "kn": "ಧನ್ಯವಾದ, ಉಳಿದವರಿಗೆ ತಿಳಿಸಿದ್ದೇನೆ.",
    },
    "claimed_notice": {
        "en": "{name} is handling it ({time}).",
        "hi": "{name} इसे देख रहे हैं ({time})।",
        "kn": "{name} ಇದನ್ನು ನೋಡಿಕೊಳ್ಳುತ್ತಿದ್ದಾರೆ ({time}).",
    },
    "already_claimed": {
        "en": "{name} is already handling it.",
        "hi": "{name} पहले से इसे देख रहे हैं।",
        "kn": "{name} ಈಗಾಗಲೇ ಇದನ್ನು ನೋಡಿಕೊಳ್ಳುತ್ತಿದ್ದಾರೆ.",
    },
    "camera_view": {
        "en": "This is what the camera sees right now.",
        "hi": "कैमरे में अभी यह दिख रहा है।",
        "kn": "ಕ್ಯಾಮೆರಾದಲ್ಲಿ ಈಗ ಇದು ಕಾಣುತ್ತಿದೆ.",
    },
    "camera_view_upright": {
        "en": " The picture is upright: if it's sideways, tap ↻ on the camera page.",
        "hi": " तस्वीर खड़ी है: अगर टेढ़ी दिखे, तो कैमरा पेज पर ↻ दबाएँ।",
        "kn": " ಚಿತ್ರ ನೇರವಾಗಿ ನಿಂತಿದೆ: ಓರೆಯಾಗಿ ಕಂಡರೆ ಕ್ಯಾಮೆರಾ ಪುಟದಲ್ಲಿ ↻ ಒತ್ತಿ.",
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
