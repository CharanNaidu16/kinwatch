"""Ask the camera: "Is Papa on the sofa?" / "पापा सोफ़े पर हैं?" answered from the latest frame.

Code spots the question shape (free); Muse Spark reads what is asked (subject, object, relation) with
minimal reasoning; SAM 3.1 finds the object and the subject in the frame; the same geometry the rules
use gives the count. The reply is a fixed per-language sentence with the photo outlined, so the family
can see the answer for themselves. Kinwatch counts people and can't tell who they are, and says so.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from backend import templates
from backend.config import Settings
from backend.models.base import Instance, Question, Segmenter
from backend.schemas import Language, Subject
from backend.snapshot import draw
from backend.vision import MIN_CONFIDENCE, Frame, Zone, is_in, is_near, is_on
from backend.watcher import SUBJECT_CONCEPTS

# --- the reply sentence ---------------------------------------------------------------------------------

_NOUN_EN = {Subject.PERSON: ("person", "people"), Subject.DOG: ("dog", "dogs"), Subject.CAT: ("cat", "cats"),
            Subject.FLAME: ("flame", "flames")}
_NOUN_HI = {Subject.PERSON: "व्यक्ति", Subject.DOG: "कुत्ता", Subject.CAT: "बिल्ली", Subject.FLAME: "आग"}
_NOUN_KN = {Subject.PERSON: "ಜನ", Subject.DOG: "ನಾಯಿ", Subject.CAT: "ಬೆಕ್ಕು", Subject.FLAME: "ಬೆಂಕಿ"}
_REL_EN = {"in": "on", "on": "on", "near": "near"}
_REL_HI = {"in": "पर", "on": "पर", "near": "के पास"}
_REL_KN = {"in": "ಮೇಲೆ", "on": "ಮೇಲೆ", "near": "ಹತ್ತಿರ"}
_CANT_TELL = {"en": " I can't tell who it is, only how many people I see.",
              "hi": " मैं यह नहीं पहचान सकता कि वह कौन है, बस गिन सकता हूँ।",
              "kn": " ಅವರು ಯಾರು ಎಂದು ನಾನು ಗುರುತಿಸಲಾರೆ, ಎಣಿಸಬಲ್ಲೆ ಮಾತ್ರ."}


def sentence(q: Question, count: int, at: str) -> str:
    """'Yes. Right now (6:02 PM) I can see 1 person on the sofa.' and its Hindi/Kannada forms."""
    lang: Language = q.language
    t = templates.spoken_time(at, lang)
    where_en = f" {_REL_EN[q.relation]} the {q.zone_object}" if q.zone_object and q.relation != "visible" else ""
    zone_hi = templates.zone_word(q.zone_object or "", "hi", oblique=True)
    zone_kn = templates.zone_word(q.zone_object or "", "kn")
    where_hi = f"{zone_hi} {_REL_HI[q.relation]} " if q.zone_object and q.relation != "visible" else "कैमरे में "
    where_kn = f"{zone_kn} {_REL_KN[q.relation]} " if q.zone_object and q.relation != "visible" else "ಕ್ಯಾಮೆರಾದಲ್ಲಿ "
    if lang == "en":
        one, many = _NOUN_EN[q.subject]
        if count:
            out = f"Yes. Right now ({t}) I can see {count} {one if count == 1 else many}{where_en}."
        else:
            nobody = "anyone" if q.subject == Subject.PERSON else f"a {one}"
            out = f"No. Right now ({t}) I can't see {nobody}{where_en or ' in the camera view'}."
    elif lang == "hi":
        noun = _NOUN_HI[q.subject]
        if count:
            out = f"हाँ। अभी ({t}) {where_hi}{count} {noun} दिख रहा है।"
        else:
            nobody = "कोई" if q.subject == Subject.PERSON else f"कोई {noun}"
            out = f"नहीं। अभी ({t}) {where_hi}{nobody} नहीं दिख रहा।"
    else:
        noun = _NOUN_KN[q.subject]
        if count:
            out = f"ಹೌದು. ಈಗ ({t}) {where_kn}{count} {noun} ಕಾಣುತ್ತಿದ್ದಾರೆ."
        else:
            nobody = "ಯಾರೂ" if q.subject == Subject.PERSON else f"ಯಾವ {noun} ಕೂಡ"
            out = f"ಇಲ್ಲ. ಈಗ ({t}) {where_kn}{nobody} ಕಾಣುತ್ತಿಲ್ಲ."
    if q.subject == Subject.PERSON and q.subject_name and count:
        out += _CANT_TELL[lang]
    return out


# --- looking ------------------------------------------------------------------------------------------

@dataclass
class Answer:
    count: int | None  # None: the object asked about isn't in view
    photo: bytes
    zone_found: bool = True
    data: dict = field(default_factory=dict)


def _confident(found: list[Instance]) -> list[Instance]:
    return [i for i in found if i.confidence is None or i.confidence >= MIN_CONFIDENCE]


def _matches(subject: Instance, zones: list[Zone], relation: str) -> bool:
    box = (subject.box.x1, subject.box.y1, subject.box.x2, subject.box.y2)
    test = {"in": lambda z: is_in(subject.mask, z), "on": lambda z: is_on(subject.mask, z),
            "near": lambda z: is_near(box, z) or is_in(subject.mask, z)}[relation]
    return any(test(z) for z in zones)


async def look(q: Question, frame: Frame, segmenter: Segmenter, settings: Settings) -> Answer:
    """One SAM call for the subject, plus one for the object when the question names one."""
    subject_call = segmenter.segment(frame.jpeg, SUBJECT_CONCEPTS[q.subject], purpose="question")
    if q.zone_object and q.relation != "visible":
        concept = settings.sam_concept(q.zone_object)
        zone_found, subjects = await asyncio.gather(
            segmenter.segment(frame.jpeg, concept, purpose="question"), subject_call)
        zones = [Zone.from_mask(i.mask, concept) for i in _confident(zone_found) if i.mask.any()]
        if not zones:
            return Answer(None, frame.jpeg, zone_found=False, data={"concept": concept})
    else:
        zones, subjects = [], await subject_call
    subjects = _confident(subjects)
    matched = [s for s in subjects if not zones or _matches(s, zones, q.relation)]
    photo = await asyncio.to_thread(draw, frame.jpeg, [z.mask for z in zones], numbered=False,
                                    subjects=[s.mask for s in matched])
    return Answer(len(matched), photo, data={"zones": len(zones), "subjects_seen": len(subjects),
                                             "matched": len(matched)})

