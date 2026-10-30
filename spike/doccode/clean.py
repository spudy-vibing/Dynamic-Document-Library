"""Output clean-up (FR-OUT-2 heading normalisation, FR-OUT-3 chatter trimming), simplified."""

from __future__ import annotations

import re

_OPEN = ("sure", "certainly", "of course", "here is", "here's", "below is")
_CLOSE = ("let me know", "i hope this", "feel free")
_H = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def clean_output(text: str, level: int, section_title: str | None) -> tuple[str, list[str]]:
    notes: list[str] = []
    paras = text.strip().split("\n\n")
    if (paras and "\n" not in paras[0] and paras[0].rstrip().endswith(":")
            and paras[0].lower().startswith(_OPEN)):
        paras.pop(0)
        notes.append("removed conversational opening")
    if paras and paras[-1].lower().startswith(_CLOSE):
        paras.pop()
        notes.append("removed conversational closing")
    text = "\n\n".join(paras)
    first = _H.search(text)
    if first and section_title and first.group(2).strip().lower() == section_title.strip().lower():
        text = (text[: first.start()] + text[first.end():]).strip()
        notes.append("removed heading repeating the section title")
    heads = _H.findall(text)
    if heads:
        shift = (level + 1) - min(len(h[0]) for h in heads)
        if shift:
            text = _H.sub(lambda m: "#" * min(6, max(1, len(m.group(1)) + shift)) + " "
                          + m.group(2), text)
            notes.append(f"shifted headings by {shift}")
    return text, notes
