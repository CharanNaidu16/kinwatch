"""Deterministic restatements and alerts in English, Hindi and Kannada.

Used by the mock Spark adapter, and as the fallback whenever a model-written restatement or alert
fails validation (PLAN.md §5.2, §5.5). Built only from the validated rule / event facts, so what
the family reads is exactly what will run. Hindi/Kannada need a native-speaker check (PLAN.md §16).
"""
from __future__ import annotations

from backend.schemas import Condition, EventFacts, Language, Rule, Subject

ZONE_WORDS: dict[str, dict[Language, str]] = {
    "bed": {"hi": "बिस्तर", "kn": "ಹಾಸಿಗೆ"},
    "sofa": {"hi": "सोफ़ा", "kn": "ಸೋಫಾ"},
    "gas stove": {"hi": "गैस चूल्हा", "kn": "ಗ್ಯಾಸ್ ಸ್ಟೌ"},
    "stove": {"hi": "चूल्हा", "kn": "ಸ್ಟೌ"},
    "main door": {"hi": "मुख्य दरवाज़ा", "kn": "ಮುಖ್ಯ ಬಾಗಿಲು"},
    "door": {"hi": "दरवाज़ा", "kn": "ಬಾಗಿಲು"},
    "refrigerator": {"hi": "फ़्रिज", "kn": "ಫ್ರಿಜ್"},
    "chair": {"hi": "कुर्सी", "kn": "ಕುರ್ಚಿ"},
}

SUBJECT_WORDS: dict[Subject, dict[Language, str]] = {
    Subject.PERSON: {"en": "someone", "hi": "कोई", "kn": "ಯಾರಾದರೂ"},
    Subject.DOG: {"en": "the dog", "hi": "कुत्ता", "kn": "ನಾಯಿ"},
    Subject.CAT: {"en": "the cat", "hi": "बिल्ली", "kn": "ಬೆಕ್ಕು"},
    Subject.FLAME: {"en": "a flame", "hi": "आग", "kn": "ಬೆಂಕಿ"},
}

_AND = {"en": " and ", "hi": " और ", "kn": " ಮತ್ತು "}
_QUESTION = {"en": "Correct?", "hi": "सही है?", "kn": "ಸರಿಯೇ?"}
_COUNTS_PEOPLE = {
    "en": "(I count people; I can't tell who is who.)",
    "hi": "(मैं लोगों को गिनता हूँ, पहचानता नहीं।)",
    "kn": "(ನಾನು ಜನರನ್ನು ಎಣಿಸುತ್ತೇನೆ, ಗುರುತಿಸುವುದಿಲ್ಲ.)",
}


# Hindi masculine -आ nouns take the oblique -े before a postposition: "सोफ़े पर", "दरवाज़े के पास".
HINDI_OBLIQUE: dict[str, str] = {
    "sofa": "सोफ़े",
    "gas stove": "गैस चूल्हे",
    "stove": "चूल्हे",
    "main door": "मुख्य दरवाज़े",
    "door": "दरवाज़े",
}


def zone_word(zone: str, lang: Language, oblique: bool = False) -> str:
    if lang == "en":
        return zone
    if oblique and lang == "hi" and zone in HINDI_OBLIQUE:
        return HINDI_OBLIQUE[zone]
    return ZONE_WORDS.get(zone, {}).get(lang, zone)


def subject_word(subject: Subject, subject_name: str | None, lang: Language) -> str:
    return subject_name or SUBJECT_WORDS[subject][lang]


def join_names(names: list[str], lang: Language) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + _AND[lang] + names[-1]


def _day_part(hour: int, lang: Language) -> str:
    if lang == "hi":
        return "सुबह" if 4 <= hour < 12 else "दोपहर" if hour < 16 else "शाम" if hour < 20 else "रात"
    return "ಬೆಳಿಗ್ಗೆ" if 4 <= hour < 12 else "ಮಧ್ಯಾಹ್ನ" if hour < 16 else "ಸಂಜೆ" if hour < 20 else "ರಾತ್ರಿ"


def spoken_time(hhmm: str, lang: Language) -> str:
    """'22:00' -> '10 PM' / 'रात 10' / 'ರಾತ್ರಿ 10'; minutes kept when non-zero ('2:03 AM')."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    h12 = hour % 12 or 12
    clock = f"{h12}:{minute:02d}" if minute else f"{h12}"
    if lang == "en":
        return f"{clock} {'AM' if hour < 12 else 'PM'}"
    return f"{_day_part(hour, lang)} {clock}"


def spoken_duration(seconds: int, lang: Language) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        n = seconds // 60
        return {"en": f"{n} minute{'s' if n != 1 else ''}", "hi": f"{n} मिनट", "kn": f"{n} ನಿಮಿಷ"}[lang]
    return {"en": f"{seconds} seconds", "hi": f"{seconds} सेकंड", "kn": f"{seconds} ಸೆಕೆಂಡ್"}[lang]


def _window_phrase(rule: Rule, lang: Language) -> str:
    w = rule.time_window
    if w is None:
        return ""
    a, b = spoken_time(w.start, lang), spoken_time(w.end, lang)
    if lang == "en":
        return f" from {a} to {b}"
    if lang == "hi":
        return f"{a} बजे से {b} बजे तक "
    return f"{a} ಗಂಟೆಯಿಂದ {b} ಗಂಟೆಯವರೆಗೆ "


def _condition_sentence(rule: Rule, names: str, lang: Language) -> str:
    subj = subject_word(rule.subject, rule.subject_name, lang)
    zone = zone_word(rule.zone_object, lang)
    zone_obl = zone_word(rule.zone_object, lang, oblique=True)
    dur = spoken_duration(rule.duration_sec, lang)
    c = rule.condition
    if c == Condition.ABSENT_FROM_ZONE:
        return {
            "en": f"If {subj} leaves the {zone} and isn't back within {dur}, I'll tell {names}.",
            "hi": f"अगर {subj} {zone} छोड़कर {dur} तक वापस नहीं आते, तो मैं {names} को बताऊँगा।",
            "kn": f"{subj} {zone} ಬಿಟ್ಟು {dur} ಒಳಗೆ ವಾಪಸ್ ಬರದಿದ್ದರೆ, {names}ಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
        }[lang]
    if c == Condition.PRESENT_IN_ZONE:
        if rule.duration_sec:
            return {
                "en": f"If {subj} stays at the {zone} for {dur}, I'll tell {names}.",
                "hi": f"अगर {subj} {dur} तक {zone_obl} के पास रहें, तो मैं {names} को बताऊँगा।",
                "kn": f"{subj} {dur} ಕಾಲ {zone} ಬಳಿ ಇದ್ದರೆ, {names}ಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
            }[lang]
        return {
            "en": f"As soon as {subj} is at the {zone}, I'll tell {names}.",
            "hi": f"जैसे ही {subj} {zone_obl} के पास दिखें, मैं {names} को बताऊँगा।",
            "kn": f"{subj} {zone} ಬಳಿ ಕಾಣಿಸಿದ ತಕ್ಷಣ {names}ಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
        }[lang]
    if c == Condition.ON_OBJECT:
        return {
            "en": f"If {subj} gets on the {zone}, I'll tell {names}.",
            "hi": f"अगर {subj} {zone_obl} पर चढ़े, तो मैं {names} को बताऊँगा।",
            "kn": f"{subj} {zone} ಮೇಲೆ ಹತ್ತಿದರೆ, {names}ಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
        }[lang]
    # NO_PERSON_NEAR
    if rule.guard is not None and rule.guard.subject == Subject.FLAME:
        return {
            "en": f"If the flame is on and nobody is near the {zone} for {dur}, I'll tell {names}.",
            "hi": f"अगर {zone_obl} पर आग जल रही हो और {dur} तक उसके पास कोई न हो, तो मैं {names} को बताऊँगा।",
            "kn": f"{zone}ನಲ್ಲಿ ಬೆಂಕಿ ಉರಿಯುತ್ತಿದ್ದು {dur} ಕಾಲ ಹತ್ತಿರ ಯಾರೂ ಇಲ್ಲದಿದ್ದರೆ, {names}ಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
        }[lang]
    if rule.guard is not None:
        g = SUBJECT_WORDS[rule.guard.subject][lang]
        return {
            "en": f"If {g} is there and nobody is near the {zone} for {dur}, I'll tell {names}.",
            "hi": f"अगर {g} दिखे और {dur} तक {zone_obl} के पास कोई न हो, तो मैं {names} को बताऊँगा।",
            "kn": f"{g} ಕಾಣಿಸಿ {dur} ಕಾಲ {zone} ಹತ್ತಿರ ಯಾರೂ ಇಲ್ಲದಿದ್ದರೆ, {names}ಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
        }[lang]
    return {
        "en": f"If nobody is near the {zone} for {dur}, I'll tell {names}.",
        "hi": f"अगर {dur} तक {zone_obl} के पास कोई न हो, तो मैं {names} को बताऊँगा।",
        "kn": f"{dur} ಕಾಲ {zone} ಹತ್ತಿರ ಯಾರೂ ಇಲ್ಲದಿದ್ದರೆ, {names}ಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
    }[lang]


def summary(rule: Rule, names: list[str]) -> str:
    """One line per rule for the "rules" list: the condition, plus the time window if there is one."""
    lang = rule.language
    sentence = _condition_sentence(rule, join_names(names, lang), lang)
    window = _window_phrase(rule, lang).strip()
    return f"{sentence} ({window})" if window else sentence


def restatement(rule: Rule, zone_count: int, names: list[str]) -> str:
    lang = rule.language
    zone = zone_word(rule.zone_object, lang)
    zone_obl = zone_word(rule.zone_object, lang, oblique=True)
    if zone_count > 1:
        return {
            "en": f"I can see {zone_count} of these ({zone}), numbered in the photo. Which one should I watch?",
            "hi": f"फ़ोटो में {zone_count} {zone} दिख रहे हैं, उन पर नंबर लगे हैं। किस पर नज़र रखूँ?",
            "kn": f"ಫೋಟೋದಲ್ಲಿ {zone_count} {zone} ಕಾಣುತ್ತಿವೆ, ಸಂಖ್ಯೆ ಹಾಕಿದ್ದೇನೆ. ಯಾವುದರ ಮೇಲೆ ಗಮನ ಇಡಲಿ?",
        }[lang]
    window = _window_phrase(rule, lang)
    first = {
        "en": f"I'll watch this {zone}{window}.",
        "hi": f"मैं {window}इस {zone_obl} पर नज़र रखूँगा।",
        "kn": f"ನಾನು {window}ಈ {zone} ಮೇಲೆ ಗಮನ ಇಡುತ್ತೇನೆ.",
    }[lang]
    parts = [first, _condition_sentence(rule, join_names(names, lang), lang)]
    if rule.subject == Subject.PERSON:
        parts.append(_COUNTS_PEOPLE[lang])
    parts.append(_QUESTION[lang])
    return " ".join(parts)


def alert(facts: EventFacts) -> str:
    lang = facts.language
    zone = zone_word(facts.zone_object, lang)
    zone_obl = zone_word(facts.zone_object, lang, oblique=True)
    start = spoken_time(facts.started_at_local, lang)
    dur = spoken_duration(facts.duration_min * 60, lang)
    c = facts.condition
    if c == Condition.ABSENT_FROM_ZONE:
        subj = subject_word(facts.subject, facts.subject_name, lang)
        msg = {
            "en": f"⚠️ {subj[0].upper() + subj[1:]} left the {zone} at {start} and hasn't come back for {dur}.",
            "hi": f"⚠️ {subj} {start} बजे {zone} छोड़कर गए और {dur} से वापस नहीं आए हैं।",
            "kn": f"⚠️ {subj} {start}ಕ್ಕೆ {zone} ಬಿಟ್ಟು ಹೋದರು, {dur}ದಿಂದ ವಾಪಸ್ ಬಂದಿಲ್ಲ.",
        }[lang]
    elif c == Condition.PRESENT_IN_ZONE:
        # Kinwatch counts people and can't recognise them, so a person is reported as "someone".
        subj = SUBJECT_WORDS[facts.subject][lang]
        msg = {
            "en": f"{subj[0].upper() + subj[1:]} was seen at the {zone} at {start}.",
            "hi": f"{start} बजे {zone_obl} के पास {subj} दिखा।",
            "kn": f"{start}ಕ್ಕೆ {zone} ಬಳಿ {subj} ಕಾಣಿಸಿದರು.",
        }[lang]
    elif c == Condition.ON_OBJECT:
        subj = subject_word(facts.subject, facts.subject_name, lang)
        msg = {
            "en": f"⚠️ {subj[0].upper() + subj[1:]} got on the {zone} at {start}.",
            "hi": f"⚠️ {subj} {start} बजे {zone_obl} पर चढ़ा।",
            "kn": f"⚠️ {subj} {start}ಕ್ಕೆ {zone} ಮೇಲೆ ಹತ್ತಿದೆ.",
        }[lang]
    else:
        msg = {
            "en": f"⚠️ Since {start}, nobody has been near the {zone} for {dur}.",
            "hi": f"⚠️ {start} बजे से {dur} तक {zone_obl} के पास कोई नहीं है।",
            "kn": f"⚠️ {start}ರಿಂದ {dur} ಕಾಲ {zone} ಹತ್ತಿರ ಯಾರೂ ಇಲ್ಲ.",
        }[lang]
        if "flame" in facts.what:
            msg = {
                "en": f"⚠️ The {zone} flame has been on since {start} and nobody has been near it for {dur}.",
                "hi": f"⚠️ {start} बजे से {zone_obl} पर आग जल रही है और {dur} से उसके पास कोई नहीं है।",
                "kn": f"⚠️ {start}ರಿಂದ {zone}ನಲ್ಲಿ ಬೆಂಕಿ ಉರಿಯುತ್ತಿದೆ, {dur}ದಿಂದ ಹತ್ತಿರ ಯಾರೂ ಇಲ್ಲ.",
            }[lang]
    if facts.visibility == "poor":
        msg += {
            "en": " The camera couldn't see clearly for part of this time.",
            "hi": " इस दौरान कुछ समय कैमरे को साफ़ नहीं दिखा।",
            "kn": " ಈ ಸಮಯದಲ್ಲಿ ಸ್ವಲ್ಪ ಹೊತ್ತು ಕ್ಯಾಮೆರಾಗೆ ಸರಿಯಾಗಿ ಕಾಣಲಿಲ್ಲ.",
        }[lang]
    if facts.check_in == "no response":
        msg += {
            "en": " There was no answer when I asked.",
            "hi": " पूछने पर कोई जवाब नहीं मिला।",
            "kn": " ಕೇಳಿದಾಗ ಯಾವುದೇ ಉತ್ತರ ಬರಲಿಲ್ಲ.",
        }[lang]
    return msg
