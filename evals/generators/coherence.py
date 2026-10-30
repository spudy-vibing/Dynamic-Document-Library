"""Shared pieces of the coherence prototypes P1-P4 (EVALS_PROPOSAL §4.3, BRD FR-COH-1..5, FR-PRV-7).

The prototypes are B1 (naive parallel) plus one coherence layer each, written from the
BRD text in plain Python, without DocCode. Their prompt text is the candidate for
DocCode's built-in frame, brief instruction and editor prompt (decision 0006).

Request layout (FR-PRV-7, HLD §5.2). Every request is ``system`` (the baselines' system
prompt, identical for every call) plus one user message whose content blocks are, in order:

1. the outline frame (FR-COH-1), byte-identical for every prompt of a document;
2. the data and attachments (``common.context_text``, the baselines' block);
3. the shared brief (FR-COH-2; P2 and P4 only);
4. under sequential flow, "the document so far" (FR-COH-3; P3 only): a header block
   and one block per earlier section, so each request's prefix extends the previous one's;
5. the per-prompt text: the position line, then the section instruction.

At most two ``cache_control`` breakpoints: one on the last static shared block (2 or 3),
and, under sequential flow, one on the last "document so far" block. They are always set;
a prefix below the model's minimum cacheable size simply does not cache (as for the
baselines). The brief call sends 1-2 (a prefix of every section's request); the editor
sends 1-3 followed by its own per-prompt part, so it also reads the sections' cache entry.

Like the baselines, prototype output is never cleaned up: data blocks are inserted by the
harness and model text is kept as written, except for the editor's checked edits (P4).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
from dataclasses import dataclass
from typing import Any

from evals.core.render import data_blocks_for
from evals.core.types import LLM, CaseSection, EvalCase, RefusalError, SectionOut
from evals.generators import common as c

FRAME = "p1_frame"
BRIEF = "p2_brief"
FLOW = "p3_flow"
EDITOR = "p4_editor"

EPHEMERAL = {"type": "ephemeral"}
MAX_BREAKPOINTS = 2
BRIEF_HEADINGS = ("Core message", "Section points", "Terms", "Avoid")
MAX_FLOW_CHARS = 60_000  # FR-COH-3 default max_flow_chars
MAX_CHANGE = 0.3  # FR-COH-4 default max_change
REPETITION_N = 8  # FR-COH-5: word 8-grams (the graders use 6-grams on purpose)
REPETITION_THRESHOLD = 2  # FR-COH-5 default repetition_threshold

# Projection estimates (characters), used only by planned_calls.
BRIEF_CHARS_ESTIMATE = 1800  # about 300 words


def prompt(name: str) -> c.PromptFile:
    return c.load_prompt(name)


# ---------------------------------------------------------------- frame (FR-COH-1)


def frame_text(case: EvalCase) -> str:
    p = prompt(FRAME)
    outline = "\n".join(
        p.render("outline_item", index=i, title=s.title, brief=s.brief) for i, s in enumerate(case.sections, 1)
    )
    return p.render(
        "frame", title=case.title, purpose=case.purpose, audience=case.audience or "-", style=case.style or "-",
        outline=outline,
    ) + "\n"


def position_line(case: EvalCase, section: CaseSection) -> str:
    index = [s.id for s in case.sections].index(section.id) + 1
    return prompt(FRAME).render("position", index=index, count=len(case.sections), title=section.title, brief=section.brief)


def section_text(case: EvalCase, section: CaseSection, dependencies: str) -> str:
    """The per-prompt part of a section call: position line, then the section's own instruction."""
    return prompt(FRAME).render(
        "user",
        position=position_line(case, section),
        section=c.section_entry(section),
        dependencies=dependencies,
        fact_rules=c.fact_rules(),
        title=section.title,
    )


def dependencies_text(case: EvalCase, section: CaseSection, dep_texts: dict[str, str]) -> str:
    if not section.depends_on:
        return ""
    p = prompt(FRAME)
    items = "\n\n".join(
        p.render("dependency_item", title=case.section(d).title, text=dep_texts[d].strip()) for d in section.depends_on
    )
    return p.render("dependencies", items=items) + "\n"


# ---------------------------------------------------------------- request layout (FR-PRV-7)


def block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def brief_prefix(brief: str) -> str:
    return prompt(BRIEF).render("prefix", brief=brief.strip()) + "\n"


def shared_blocks(case: EvalCase, *, brief: str | None = None, so_far: list[str] | None = None) -> list[dict[str, Any]]:
    """The shared, document-wide part of the first user message, with at most two breakpoints."""
    blocks = [block(frame_text(case)), block(c.context_text(case))]
    if brief is not None:
        blocks.append(block(brief_prefix(brief)))
    blocks[-1]["cache_control"] = dict(EPHEMERAL)  # breakpoint 1: the last static shared block
    if so_far:
        blocks += [block(t) for t in so_far]
        blocks[-1]["cache_control"] = dict(EPHEMERAL)  # breakpoint 2: the end of the document so far
    return blocks


def messages(case: EvalCase, text: str, *, brief: str | None = None, so_far: list[str] | None = None) -> list[dict[str, Any]]:
    return [{"role": "user", "content": [*shared_blocks(case, brief=brief, so_far=so_far), block(text)]}]


def shared_chars(case: EvalCase, *, brief_chars: int = 0) -> int:
    """System prompt plus the static shared blocks, for the cost projection."""
    n = len(c.system_prompt()) + len(frame_text(case)) + len(c.context_text(case))
    if brief_chars:
        n += len(brief_prefix("x" * brief_chars))
    return n


# ---------------------------------------------------------------- writing sections


@dataclass
class Draft:
    texts: dict[str, str]
    stops: dict[str, str | None]

    def warnings(self) -> list[str]:
        return [f"section {sid}: stopped at max_tokens" for sid, r in self.stops.items() if r == "max_tokens"]


async def write_parallel(case: EvalCase, llm: LLM, *, brief: str | None = None) -> Draft:
    """B1's scheduling (concurrent, respecting depends_on) with the frame and optional brief in the prefix."""
    texts: dict[str, str] = {}
    stops: dict[str, str | None] = {}
    tasks: dict[str, asyncio.Task[None]] = {}

    async def write(section: CaseSection) -> None:
        for dep in section.depends_on:  # dependencies come earlier, so their tasks exist
            await tasks[dep]
        text = section_text(case, section, dependencies_text(case, section, texts))
        resp = await llm.complete(role="generate", node_id=section.id, system=c.system_prompt(),
                                  messages=messages(case, text, brief=brief))
        texts[section.id] = resp.text
        stops[section.id] = resp.call.stop_reason

    for s in case.sections:
        tasks[s.id] = asyncio.ensure_future(write(s))
    await gather_or_raise(list(tasks.values()))
    return Draft(texts, stops)


async def gather_or_raise(tasks: list[asyncio.Task[None]]) -> None:
    """Wait for every task; on the first failure cancel the rest and re-raise it (as B1 does)."""
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    failed = [t for t in done if not t.cancelled() and t.exception() is not None]
    if failed:
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in tasks:  # retrieve every exception so none is reported as unhandled
            if t.done() and not t.cancelled():
                t.exception()
        raise failed[0].exception()  # type: ignore[misc]


def section_markdown(case: EvalCase, section: CaseSection, text: str) -> str:
    """An earlier section as the document so far shows it: data blocks as Markdown, then the model text."""
    parts = [b.text for b in data_blocks_for(case, section.id)]
    if text.strip():
        parts.append(text.strip())
    return "\n\n".join(parts)


def first_paragraph(text: str) -> str:
    for para in re.split(r"\n\s*\n", text.strip()):
        if para.strip():
            return para.strip()
    return ""


def so_far_blocks(case: EvalCase, written: list[tuple[CaseSection, str]], max_chars: int = MAX_FLOW_CHARS) -> list[str]:
    """Header plus one block per earlier section; above ``max_chars`` the oldest are cut to their first paragraph.

    The cut set only grows as the document grows, so the prefix stays append-only until
    the limit is first reached.
    """
    if not written:
        return []
    p = prompt(FLOW)
    full = [section_markdown(case, s, t) for s, t in written]
    bodies = list(full)
    for i, (_, text) in enumerate(written):
        if sum(len(b) for b in bodies) <= max_chars:
            break
        bodies[i] = p.render("cut", first_paragraph=first_paragraph(text) or "(no text)")
    head = p.render("header") + "\n"
    return [head] + [
        p.render("written_section", id=s.id, title=s.title, text=body) + "\n" for (s, _), body in zip(written, bodies)
    ]


def flow_section_text(case: EvalCase, section: CaseSection) -> str:
    return section_text(case, section, prompt(FLOW).render("continue") + "\n")


async def write_sequential(case: EvalCase, llm: LLM, *, max_chars: int = MAX_FLOW_CHARS) -> Draft:
    """FR-COH-3: document order, each call seeing every earlier section in its prefix."""
    texts: dict[str, str] = {}
    stops: dict[str, str | None] = {}
    written: list[tuple[CaseSection, str]] = []
    for s in case.sections:
        resp = await llm.complete(
            role="generate", node_id=s.id, system=c.system_prompt(),
            messages=messages(case, flow_section_text(case, s), so_far=so_far_blocks(case, written, max_chars)),
        )
        texts[s.id] = resp.text
        stops[s.id] = resp.call.stop_reason
        written.append((s, resp.text))
    return Draft(texts, stops)


# ---------------------------------------------------------------- shared brief (FR-COH-2)


def brief_instruction(case: EvalCase) -> str:
    return prompt(BRIEF).render("instruction", section_ids=", ".join(s.id for s in case.sections))


def brief_problems(case: EvalCase, text: str) -> list[str]:
    """What makes a brief invalid: missing or out-of-order headings, missing section bullets."""
    found = [m.group(1).strip() for m in re.finditer(r"^##[ \t]+(.+?)[ \t]*$", text, re.M)]
    problems = []
    wanted = [h for h in found if h in BRIEF_HEADINGS]
    if wanted != list(BRIEF_HEADINGS):
        problems.append(f"headings {found} are not exactly {list(BRIEF_HEADINGS)}")
    points = re.search(r"^##[ \t]+Section points[ \t]*$(.*?)(?=^##[ \t]|\Z)", text, re.M | re.S)
    body = points.group(1) if points else ""
    missing = [s.id for s in case.sections if not re.search(rf"^\s*[-*][ \t]+`?{re.escape(s.id)}`?\s*:", body, re.M)]
    if missing:
        problems.append(f"no section point for {', '.join(missing)}")
    return problems


async def write_brief(case: EvalCase, llm: LLM, warnings: list[str]) -> str | None:
    """Run ``_brief`` first. An empty brief is dropped; an invalid one is kept, with a warning."""
    resp = await llm.complete(role="brief", node_id="_brief", system=c.system_prompt(),
                              messages=messages(case, brief_instruction(case)))
    if resp.call.stop_reason == "max_tokens":
        warnings.append("brief: stopped at max_tokens")
    if not resp.text.strip():
        warnings.append("brief: no text; sections written without a brief")
        return None
    for problem in brief_problems(case, resp.text):
        warnings.append(f"brief: {problem}")
    return resp.text.strip()


# ---------------------------------------------------------------- repetition (FR-COH-5)

STOP_WORDS = frozenset(
    "a an and are as at be been but by can do for from had has have he her his i if in into is it its "
    "more most no not of on or our over so such than that the their them then there these they this "
    "to up was we were what when which while who will with would you your".split()
)
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


@dataclass(frozen=True)
class Finding:
    a: str
    b: str
    count: int
    phrase: str

    def warning(self) -> str:
        return f'repetition: sections {self.a} and {self.b} share {self.count} {REPETITION_N}-gram(s), e.g. "{self.phrase}"'


def ngrams(text: str, n: int = REPETITION_N) -> list[tuple[str, ...]]:
    """Word n-grams, case- and punctuation-insensitive, skipping stop-word-only n-grams."""
    words = _WORD.findall(text.lower())
    grams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
    return [g for g in grams if not all(w in STOP_WORDS for w in g)]


def find_repetition(texts: dict[str, str], *, n: int = REPETITION_N, threshold: int = REPETITION_THRESHOLD) -> list[Finding]:
    """Every pair of model-written sections sharing at least ``threshold`` n-grams, in outline order."""
    ids = [i for i, t in texts.items() if t.strip()]
    grams = {i: ngrams(texts[i], n) for i in ids}
    out = []
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            a, b = ids[x], ids[y]
            shared = set(grams[a]) & set(grams[b])
            if len(shared) >= threshold:
                first = next(g for g in grams[a] if g in shared)
                out.append(Finding(a, b, len(shared), " ".join(first)))
    return out


def model_texts(sections: list[SectionOut]) -> dict[str, str]:
    return {s.id: s.model_text() for s in sections}


# ---------------------------------------------------------------- editor (FR-COH-4)

_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


@dataclass
class Edit:
    section_id: str
    find: str
    replace: str
    reason: str = ""


def parse_editor_reply(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``{"edits": [...], "notes": [...]}``; a code fence or surrounding prose is tolerated. Raises ValueError."""
    s = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", s, re.S)
    if fence:
        s = fence.group(1)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    try:
        data = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e.msg}") from e
    if not isinstance(data, dict) or not isinstance(data.get("edits", []), list):
        raise ValueError('expected an object with an "edits" list')
    notes = data.get("notes", [])
    if not isinstance(notes, list):
        raise ValueError('"notes" must be a list')
    return list(data.get("edits", [])), [str(n) for n in notes]


def numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _NUM.findall(text)}


def recheck(case: EvalCase, section: CaseSection, before: str, after: str) -> str | None:
    """The block's checks, run again after editing (a stand-in for FR-OUT-4 checks): why to revert, or None."""
    if not after.strip():
        return "the edits would empty the section"
    allowed = numbers(before) | numbers(c.context_text(case))
    added = sorted(numbers(after) - allowed)
    if added:
        return f"the edits add figures not in the data or the draft: {', '.join(added)}"
    for phrase in section.constraints.forbidden_phrases:
        if phrase.lower() in after.lower() and phrase.lower() not in before.lower():
            return f'the edits add the forbidden phrase "{phrase}"'
    limit = section.constraints.max_words
    if limit and len(after.split()) > limit * 1.1 >= len(before.split()):
        return f"the edits push the section over {limit} words"
    return None


def model_block(section: SectionOut) -> Any:
    for b in section.blocks:
        if b.origin == "model":
            return b
    return None


def apply_edits(
    case: EvalCase, sections: list[SectionOut], raw_edits: list[Any], *, max_change: float = MAX_CHANGE
) -> tuple[dict[str, str], list[str]]:
    """Apply checked edits in place. Returns (pre-edit text of each changed section, warnings).

    Every applied edit is also listed in the warnings (find, replace, reason), because
    ``GenerationResult`` has no field for pre-edit text.

    An edit is applied only if its section exists and has model text, ``find`` matches
    exactly once in that model-written block (data blocks are read-only), and, after all
    edits, the block's change ratio is at most ``max_change`` and its checks still pass;
    otherwise it is rejected (or the section's edits reverted) with a warning.
    """
    by_id = {s.id: s for s in sections}
    warnings: list[str] = []
    working: dict[str, str] = {}
    counts: dict[str, int] = {}
    applied: dict[str, list[Edit]] = {}
    for i, raw in enumerate(raw_edits, 1):
        try:
            e = Edit(str(raw["section_id"]), raw["find"], raw["replace"], str(raw.get("reason", "")))
            if not isinstance(e.find, str) or not isinstance(e.replace, str):
                raise TypeError
        except (KeyError, TypeError):
            warnings.append(f"editor: rejected edit {i}: malformed (needs section_id, find, replace)")
            continue
        sec = by_id.get(e.section_id)
        blk = model_block(sec) if sec is not None else None
        if sec is None:
            warnings.append(f"editor: rejected edit {i}: unknown section {e.section_id!r}")
            continue
        if blk is None:
            warnings.append(f"editor: rejected edit {i}: section {e.section_id} has no model-written text")
            continue
        text = working.get(e.section_id, blk.text)
        hits = text.count(e.find) if e.find else 0
        if hits != 1:
            read_only = e.find and any(e.find in b.text for b in sec.blocks if b.origin != "model")
            why = "targets a read-only block" if read_only else f"find matches {hits} times, not exactly once"
            warnings.append(f"editor: rejected edit {i} to section {e.section_id}: {why}")
            continue
        working[e.section_id] = text.replace(e.find, e.replace, 1)
        counts[e.section_id] = counts.get(e.section_id, 0) + 1
        applied.setdefault(e.section_id, []).append(e)

    before: dict[str, str] = {}
    for sid, new in working.items():
        blk = model_block(by_id[sid])
        old = blk.text
        ratio = 1 - difflib.SequenceMatcher(None, old, new).ratio()
        if ratio > max_change:
            warnings.append(f"editor: reverted {counts[sid]} edit(s) to section {sid}: change ratio {ratio:.2f} > {max_change}")
            continue
        why = recheck(case, case.section(sid), old, new)
        if why:
            warnings.append(f"editor: reverted {counts[sid]} edit(s) to section {sid}: {why}")
            continue
        if new != old:
            before[sid] = old
            blk.text = new
            warnings.append(f"editor: applied {counts[sid]} edit(s) to section {sid} (change ratio {ratio:.2f})")
            # The edits themselves, so the pre-edit text can be reconstructed from the stored result.
            warnings += [f"editor edit {sid}: {e.find!r} -> {e.replace!r} ({e.reason})" for e in applied[sid]]
    return before, warnings


def draft_text(sections: list[SectionOut]) -> str:
    p = prompt(EDITOR)
    out = []
    for s in sections:
        parts = []
        for b in s.blocks:
            if b.origin == "model":
                parts.append(p.render("editable", text=b.text.strip()))
            elif b.origin in ("data", "static"):
                parts.append(p.render("read_only", kind=b.kind, text=b.text.strip()))
        out.append(p.render("draft_section", id=s.id, title=s.title, blocks="\n".join(parts) or "(no text)"))
    return "\n\n".join(out)


def findings_text(findings: list[Finding]) -> str:
    p = prompt(EDITOR)
    if not findings:
        return p.render("no_findings")
    items = "\n".join(p.render("finding_item", a=f.a, b=f.b, count=f.count, phrase=f.phrase) for f in findings)
    return p.render("findings", items=items)


def editor_text(sections: list[SectionOut], findings: list[Finding], max_change: float = MAX_CHANGE) -> str:
    return prompt(EDITOR).render(
        "user", max_change_pct=int(round(max_change * 100)), findings=findings_text(findings), draft=draft_text(sections)
    )


async def run_editor(
    case: EvalCase, llm: LLM, sections: list[SectionOut], *, brief: str | None, findings: list[Finding],
    max_change: float = MAX_CHANGE,
) -> tuple[dict[str, str], list[str]]:
    """One ``_editor`` call over the whole draft; malformed JSON gets one retry (FR-COH-4).

    A refusal or an unusable reply leaves the draft unedited with an editor warning, as
    FR-COH-4 says. Infra errors (exhausted retries, served-model mismatch) still fail the
    slot, because the harness never scores infra failures.
    """
    msgs = messages(case, editor_text(sections, findings, max_change), brief=brief)
    error = ""
    for attempt in (1, 2):
        try:
            resp = await llm.complete(role="editor", node_id="_editor", system=c.system_prompt(), messages=msgs)
        except RefusalError as e:
            return {}, [f"editor: refused ({e.call.refusal_category}); draft left unedited"]
        try:
            edits, notes = parse_editor_reply(resp.text)
        except ValueError as e:
            error = str(e)
            if attempt == 1:
                msgs = msgs + [
                    {"role": "assistant", "content": resp.text if resp.text.strip() else "[no text returned]"},
                    {"role": "user", "content": prompt(EDITOR).render("retry", error=error)},
                ]
            continue
        before, warnings = apply_edits(case, sections, edits, max_change=max_change)
        return before, warnings + [f"editor note: {n}" for n in notes]
    return {}, [f"editor: unusable reply after one retry ({error}); draft left unedited"]


# ---------------------------------------------------------------- projection helpers


def draft_chars(case: EvalCase, visible_chars_per_section: int) -> int:
    data = sum(len(b.text) for s in case.sections for b in data_blocks_for(case, s.id))
    return data + len(case.sections) * (visible_chars_per_section + 120)

