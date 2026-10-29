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
        "en": "I can't see a {zone} in the camera view (this is what the camera sees). Point the phone at it, "
              "or tap ↻ on the camera page if the picture is sideways, and send your message again.",
        "hi": "मुझे कैमरे में {zone} नहीं दिख रहा (कैमरे में यह दिख रहा है)। फ़ोन को उसकी तरफ़ करें, या तस्वीर "
              "टेढ़ी हो तो कैमरा पेज पर ↻ दबाएँ, और मैसेज दोबारा भेजें।",
        "kn": "ಕ್ಯಾಮೆರಾದಲ್ಲಿ {zone} ಕಾಣುತ್ತಿಲ್ಲ (ಕ್ಯಾಮೆರಾಗೆ ಇದು ಕಾಣುತ್ತಿದೆ). ಫೋನ್ ಅನ್ನು ಅದರ ಕಡೆ ತಿರುಗಿಸಿ, "
              "ಅಥವಾ ಚಿತ್ರ ಓರೆಯಾಗಿದ್ದರೆ ಕ್ಯಾಮೆರಾ ಪುಟದಲ್ಲಿ ↻ ಒತ್ತಿ, ನಂತರ ಮೆಸೇಜ್ ಮತ್ತೆ ಕಳುಹಿಸಿ.",
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
    "resend": {
        "en": "⏰ Nobody has tapped \"I've got it\" for {wait}.",
        "hi": "⏰ {wait} से किसी ने \"मैं देख लूँगा\" नहीं दबाया।",
        "kn": "⏰ {wait}ದಿಂದ ಯಾರೂ \"ನಾನು ನೋಡ್ತೀನಿ\" ಒತ್ತಿಲ್ಲ.",
    },
    "escalation": {
        "en": "⚠️ {names} didn't answer, so I'm sending this to you too:",
        "hi": "⚠️ {names} ने जवाब नहीं दिया, इसलिए आपको भी भेज रहा हूँ:",
        "kn": "⚠️ {names} ಉತ್ತರಿಸಲಿಲ್ಲ, ಹಾಗಾಗಿ ನಿಮಗೂ ಕಳುಹಿಸುತ್ತಿದ್ದೇನೆ:",
    },
    "alert_expired": {
        "en": "Nobody claimed the alert from {time}, so I've stopped reminding. "
              "You can still tap \"I've got it\" on it.",
        "hi": "{time} बजे वाले अलर्ट को किसी ने नहीं लिया, इसलिए मैंने याद दिलाना बंद कर दिया है। "
              "आप अब भी उस पर \"मैं देख लूँगा\" दबा सकते हैं।",
        "kn": "{time}ರ ಅಲರ್ಟ್ ಅನ್ನು ಯಾರೂ ತೆಗೆದುಕೊಳ್ಳಲಿಲ್ಲ, ಹಾಗಾಗಿ ನೆನಪಿಸುವುದನ್ನು ನಿಲ್ಲಿಸಿದ್ದೇನೆ. "
              "ಆದರೂ ಅದರಲ್ಲಿ \"ನಾನು ನೋಡ್ತೀನಿ\" ಒತ್ತಬಹುದು.",
    },
    "rules_header": {"en": "Your rules:", "hi": "आपके नियम:", "kn": "ನಿಮ್ಮ ನಿಯಮಗಳು:"},
    "rules_none": {
        "en": "There are no rules yet. Tell me what to watch, for example: tell me if Papa leaves the bed "
              "at night and isn't back in 10 minutes.",
        "hi": "अभी कोई नियम नहीं है। बताइए किस पर नज़र रखूँ, जैसे: पापा रात को बिस्तर से उठें और "
              "10 मिनट में वापस न आएँ तो मुझे बताना।",
        "kn": "ಇನ್ನೂ ಯಾವ ನಿಯಮವೂ ಇಲ್ಲ. ಯಾವುದರ ಮೇಲೆ ಗಮನ ಇಡಬೇಕು ಎಂದು ಹೇಳಿ, ಉದಾಹರಣೆಗೆ: ಅಪ್ಪ ರಾತ್ರಿ ಹಾಸಿಗೆ "
              "ಬಿಟ್ಟು 10 ನಿಮಿಷದಲ್ಲಿ ವಾಪಸ್ ಬರದಿದ್ದರೆ ತಿಳಿಸು.",
    },
    "rules_help": {
        "en": "To change one, send: pause {n}, resume {n} or delete {n}.",
        "hi": "बदलने के लिए भेजें: pause {n}, resume {n} या delete {n}।",
        "kn": "ಬದಲಾಯಿಸಲು ಕಳುಹಿಸಿ: pause {n}, resume {n} ಅಥವಾ delete {n}.",
    },
    "rule_on": {"en": "▶️ on", "hi": "▶️ चालू", "kn": "▶️ ಚಾಲೂ"},
    "rule_off": {"en": "⏸ paused", "hi": "⏸ रुका हुआ", "kn": "⏸ ನಿಲ್ಲಿಸಲಾಗಿದೆ"},
    "rule_paused": {
        "en": "⏸ Rule {n} is paused. Send \"resume {n}\" to turn it back on.",
        "hi": "⏸ नियम {n} रोक दिया गया है। फिर से चालू करने के लिए \"resume {n}\" भेजें।",
        "kn": "⏸ ನಿಯಮ {n} ನಿಲ್ಲಿಸಲಾಗಿದೆ. ಮತ್ತೆ ಚಾಲೂ ಮಾಡಲು \"resume {n}\" ಕಳುಹಿಸಿ.",
    },
    "rule_resumed": {
        "en": "▶️ Rule {n} is on again: {summary}",
        "hi": "▶️ नियम {n} फिर से चालू: {summary}",
        "kn": "▶️ ನಿಯಮ {n} ಮತ್ತೆ ಚಾಲೂ: {summary}",
    },
    "rule_deleted": {
        "en": "🗑 Rule {n} is deleted.",
        "hi": "🗑 नियम {n} हटा दिया गया है।",
        "kn": "🗑 ನಿಯಮ {n} ಅಳಿಸಲಾಗಿದೆ.",
    },
    "rule_already_on": {"en": "Rule {n} is already on.", "hi": "नियम {n} पहले से चालू है।",
                        "kn": "ನಿಯಮ {n} ಈಗಾಗಲೇ ಚಾಲೂ ಇದೆ."},
    "rule_already_paused": {"en": "Rule {n} is already paused.", "hi": "नियम {n} पहले से रुका हुआ है।",
                            "kn": "ನಿಯಮ {n} ಈಗಾಗಲೇ ನಿಲ್ಲಿಸಲಾಗಿದೆ."},
    "rule_unknown": {
        "en": "I don't have a rule {n}. Send \"rules\" to see them.",
        "hi": "नियम {n} मेरे पास नहीं है। सभी नियम देखने के लिए \"rules\" भेजें।",
        "kn": "ನಿಯಮ {n} ನನ್ನ ಬಳಿ ಇಲ್ಲ. ಎಲ್ಲ ನಿಯಮಗಳನ್ನು ನೋಡಲು \"rules\" ಕಳುಹಿಸಿ.",
    },
    "rule_no_zone": {
        "en": "I no longer have the outlined photo for rule {n}, so I can't watch it. Send the rule again "
              "and I'll make a new one.",
        "hi": "नियम {n} की घेरे वाली फ़ोटो अब मेरे पास नहीं है, इसलिए मैं उस पर नज़र नहीं रख सकता। "
              "नियम दोबारा भेजें, मैं नया बना दूँगा।",
        "kn": "ನಿಯಮ {n}ರ ಗುರುತು ಹಾಕಿದ ಫೋಟೋ ಈಗ ನನ್ನ ಬಳಿ ಇಲ್ಲ, ಹಾಗಾಗಿ ಗಮನಿಸಲಾರೆ. ನಿಯಮವನ್ನು ಮತ್ತೆ "
              "ಕಳುಹಿಸಿ, ಹೊಸದು ಮಾಡುತ್ತೇನೆ.",
    },
    "question_unsupported": {
        "en": "I can only tell you what the camera sees right now, for example: \"Is anyone on the sofa?\" "
              "This is the picture now.",
        "hi": "मैं बस यह बता सकता हूँ कि कैमरे में अभी क्या दिख रहा है, जैसे: \"क्या सोफ़े पर कोई है?\" "
              "यह अभी की तस्वीर है।",
        "kn": "ಕ್ಯಾಮೆರಾದಲ್ಲಿ ಈಗ ಏನು ಕಾಣುತ್ತಿದೆ ಎಂಬುದನ್ನು ಮಾತ್ರ ಹೇಳಬಲ್ಲೆ, ಉದಾಹರಣೆಗೆ: \"ಸೋಫಾ ಮೇಲೆ ಯಾರಾದರೂ "
              "ಇದ್ದಾರಾ?\" ಇದು ಈಗಿನ ಚಿತ್ರ.",
    },
    "too_dark_now": {
        "en": "It's too dark for me to see right now. This is the picture now.",
        "hi": "अभी इतना अंधेरा है कि मुझे साफ़ नहीं दिख रहा। यह अभी की तस्वीर है।",
        "kn": "ಈಗ ತುಂಬಾ ಕತ್ತಲಿದೆ, ನನಗೆ ಸರಿಯಾಗಿ ಕಾಣುತ್ತಿಲ್ಲ. ಇದು ಈಗಿನ ಚಿತ್ರ.",
    },
    "zone_not_in_view": {
        "en": "I can't see a {zone} in the camera view right now. This is the picture now.",
        "hi": "मुझे अभी कैमरे में {zone} नहीं दिख रहा। यह अभी की तस्वीर है।",
        "kn": "ಕ್ಯಾಮೆರಾದಲ್ಲಿ ಈಗ {zone} ಕಾಣುತ್ತಿಲ್ಲ. ಇದು ಈಗಿನ ಚಿತ್ರ.",
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
    "camera_lost": {
        "en": "📷 The camera stopped sending pictures at {time}, so I can't watch right now. "
              "Open the camera link on the camera phone again (in Chrome) and keep that page on screen.",
        "hi": "📷 कैमरे ने {time} से तस्वीरें भेजना बंद कर दिया है, इसलिए मैं अभी नज़र नहीं रख पा रहा। "
              "कैमरा फ़ोन पर कैमरा लिंक फिर से खोलें (Chrome में) और वह पेज स्क्रीन पर खुला रखें।",
        "kn": "📷 {time}ರಿಂದ ಕ್ಯಾಮೆರಾ ಚಿತ್ರಗಳನ್ನು ಕಳುಹಿಸುತ್ತಿಲ್ಲ, ಹಾಗಾಗಿ ನಾನು ಈಗ ಗಮನಿಸಲಾರೆ. "
              "ಕ್ಯಾಮೆರಾ ಫೋನ್‌ನಲ್ಲಿ ಕ್ಯಾಮೆರಾ ಲಿಂಕ್ ಮತ್ತೆ ತೆರೆಯಿರಿ (Chrome ನಲ್ಲಿ) ಮತ್ತು ಆ ಪುಟವನ್ನು ತೆರೆದೇ ಇಡಿ.",
    },
    "camera_back": {
        "en": "📷 The camera is back. I'm watching again.",
        "hi": "📷 कैमरा वापस चालू है। मैं फिर से नज़र रख रहा हूँ।",
        "kn": "📷 ಕ್ಯಾಮೆರಾ ಮತ್ತೆ ಬಂದಿದೆ. ನಾನು ಮತ್ತೆ ಗಮನಿಸುತ್ತಿದ್ದೇನೆ.",
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
