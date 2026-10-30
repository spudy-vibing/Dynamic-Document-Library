"""Authoring API: Document, Section, Prompt, Value, ctx, expansions, tokens (BRD §4.1-4.4, §4.13-4.14).

Spike quality: one module, no typing discipline, enough to try the API on real users.
"""

from __future__ import annotations

import contextvars
import json
import re
import secrets
import string
import weakref
from contextlib import contextmanager
from typing import Any, Callable, Iterable

from .errors import (
    DocumentFrozenError, DuplicateIdError, NotExecutedError,
)

# ---------------------------------------------------------------- tokens (FR-REF-1)

L, R = "⟦", "⟧"
TOKEN_RE = re.compile(
    L + r"doccode:(?:ref:(?P<key>[0-9a-f]{12})(?:\|max_chars=(?P<n>[1-9][0-9]*))?"
    r"|ctx:(?P<name>[A-Za-z_][A-Za-z0-9_]*))" + R)
MISUSE_RE = re.compile(r"[⟦⟧]|doccode:(?:ref|ctx|repr)", re.IGNORECASE)

_REGISTRY: "weakref.WeakValueDictionary[str, Node]" = weakref.WeakValueDictionary()

# Active container stack (FR-DOC-4) and expansion context (FR-DOC-9).
_STACK: contextvars.ContextVar[tuple] = contextvars.ContextVar("doccode_stack", default=())
_EXPANSION: contextvars.ContextVar = contextvars.ContextVar("doccode_expansion", default=None)

_HELPERS: "weakref.WeakSet[Prompt]" = weakref.WeakSet()
_SEQ = [0]


def _next_seq() -> int:
    _SEQ[0] += 1
    return _SEQ[0]


SHARED_NAMESPACE = False  # BRD FR-DAG-1 says True; see Document.__init__

_CONFIG: dict[str, Any] ={"provider": None, "context": {}, "model": None}


def configure(*, provider=None, context: dict | None = None, model: str | None = None,
              **_ignored) -> None:
    """Process-wide defaults (FR-CFG-1, subset)."""
    if provider is not None:
        _CONFIG["provider"] = provider
    if context is not None:
        _CONFIG["context"] = dict(context)
    if model is not None:
        _CONFIG["model"] = model


def has_token(text: str) -> bool:
    return bool(TOKEN_RE.search(text))


def slugify(title: str) -> str:
    out, dash = [], False
    for ch in title.lower():
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash:
            out.append("-")
            dash = True
    return "".join(out).strip("-")


def to_text(value: Any) -> str:
    """FR-CTX-2 conversion."""
    if hasattr(value, "__doccode__"):
        return value.__doccode__()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return str(value)


# ---------------------------------------------------------------- ctx (FR-CTX-4)

class CtxRef:
    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return f"{L}doccode:ctx:{self.name}{R}"

    def __format__(self, spec: str) -> str:
        if spec:
            raise TypeError(f"ctx.{self.name} does not take a format spec ({spec!r}); format the "
                            "value before putting it in context")
        return str(self)

    def __repr__(self) -> str:
        return f"<ctx reference {self.name!r}>"


class _Ctx:
    def __getattr__(self, name: str) -> CtxRef:
        if name.startswith("__"):
            raise AttributeError(name)
        return CtxRef(name)

    def __getitem__(self, name: str) -> CtxRef:
        return CtxRef(name)


ctx = _Ctx()


# ---------------------------------------------------------------- graph nodes

class Node:
    """Anything that can be referenced: Prompt, Value, Expansion."""

    kind = "node"

    def __init__(self, id: str | None):
        self.key = secrets.token_hex(6)
        self._explicit_id = id
        self.id: str | None = id
        self.doc: Document | None = None
        self.parent: Container | None = None  # registration point (None = helper)
        _REGISTRY[self.key] = self
        self._done = False
        self._result: Any = None

    # f-string support (FR-REF-1, FR-REF-9)
    def __str__(self) -> str:
        return f"{L}doccode:ref:{self.key}{R}"

    def __format__(self, spec: str) -> str:
        if spec:
            raise TypeError(
                f"{self.kind} {self.id or '(unnamed)'} was formatted with {spec!r}; references "
                "take no format spec. For a Value, format inside the function instead.")
        return str(self)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={self.id!r} {L}doccode:repr:{self.key}{R}>"

    def ref(self, *, max_chars: int) -> str:
        return f"{L}doccode:ref:{self.key}|max_chars={int(max_chars)}{R}"

    def deps(self) -> list[Node]:
        return []


class Prompt(Node):
    kind = "prompt"

    def __init__(self, instruction: str, *, id: str | None = None, model: str | None = None,
                 system: str | None = None, effort: str | None = None,
                 max_tokens: int | None = None, depends_on: Iterable[Node] = (),
                 refs: dict | None = None, context: dict | None = None, cache: bool = True,
                 visible: bool = True, provider=None, attachments=None, checks=(),
                 on_check_fail: str = "fail", raw: bool = False, frame: bool | None = None,
                 output_schema=None):
        super().__init__(id)
        if not isinstance(instruction, str):
            instruction = str(instruction)
        self.instruction = instruction
        self.model, self.system, self.effort, self.max_tokens = model, system, effort, max_tokens
        self.depends_on = tuple(depends_on)
        self.refs = dict(refs or {})
        self.context = dict(context or {})
        self.visible, self.provider, self.attachments = visible, provider, attachments
        self.checks = tuple(checks)
        self.on_check_fail, self.raw, self.frame = on_check_fail, raw, frame
        stack = _STACK.get()
        if stack:  # FR-PRM-2: register into innermost active container
            stack[-1]._add_prompt(self)
        else:  # FR-PRM-10 orphan tracking
            self._seq = _next_seq()
            _HELPERS.add(self)

    @property
    def output(self) -> str:
        if not self._done:
            raise NotExecutedError(f"Prompt {self.id or '(unnamed)'} has not run yet.",
                                   ids=(self.id or "",), hint="call doc.execute() first")
        return self._result

    def texts(self) -> list[str]:
        return [self.instruction] + ([self.system] if self.system else [])

    def deps(self) -> list[Node]:
        out: list[Node] = list(self.depends_on) + [r for r in self.refs.values()
                                                   if isinstance(r, Node)]
        for c in self.checks:
            out.extend(getattr(c, "sources", lambda: ())())
        return out


class Value(Node):
    """FR-VAL-1: Python compute node. fn(*dep_results)."""

    kind = "value"

    def __init__(self, fn: Callable, *deps: Node, id: str | None = None):
        super().__init__(id)
        self.fn, self._deps = fn, tuple(deps)

    @property
    def result(self) -> Any:
        if not self._done:
            raise NotExecutedError(f"Value {self.id or '(unnamed)'} has not run yet.",
                                   ids=(self.id or "",))
        return self._result

    def deps(self) -> list[Node]:
        return list(self._deps)

    def texts(self) -> list[str]:
        return []


# ---------------------------------------------------------------- static blocks

class Text:
    def __init__(self, markdown: str, container: "Container"):
        self.markdown, self.container = str(markdown), container
        self.doc, self.parent, self.context = container.doc, container, {}


class Table:
    def __init__(self, data, columns, caption):
        self.data, self.columns, self.caption = data, columns, caption


class Metric:
    def __init__(self, label, value, delta, unit):
        self.label, self.value, self.delta, self.unit = label, value, delta, unit


# ---------------------------------------------------------------- containers

class Container:
    """Document root, Section or Expansion: an ordered list of children."""

    def __init__(self, doc: "Document | None", parent: "Container | None"):
        self.doc = doc
        self.parent = parent
        self.children: list = []

    # chain helpers
    def section_chain(self) -> list["Section"]:
        out, c = [], self
        while c is not None:
            if isinstance(c, Section):
                out.append(c)
            c = c.parent
        return list(reversed(out))

    def expansion_prefix(self) -> str:
        c = self
        while c is not None:
            if isinstance(c, Expansion):
                return c.id + "/"
            c = c.parent
        return ""

    def nearest_section(self) -> "Section | None":
        c = self
        while c is not None and not isinstance(c, Section):
            c = c.parent
        return c

    def _check_open(self) -> None:
        self.doc._assert_open(self)

    def _add_prompt(self, p: Prompt) -> None:
        self._check_open()
        if p.parent is not None:
            raise DuplicateIdError(f"Prompt {p.id} is already registered.", ids=(p.id or "",))
        p.doc, p.parent = self.doc, self
        sec = self.nearest_section()
        base = sec.id if sec else (self.expansion_prefix().rstrip("/") or "_doc")
        n = self.doc._prompt_counters.get(base, 0) + 1  # FR-PRM-5: index among section prompts
        self.doc._prompt_counters[base] = n
        if p._explicit_id:
            self.doc._claim(p._explicit_id)
        else:
            p.id = self.doc._claim(f"{base}.{n}")
            self.doc._generated_ids.append(p.id)
        self.children.append(p)

    def text(self, markdown: str) -> None:
        self._check_open()
        self.children.append(Text(markdown, self))

    def table(self, data, *, columns=None, caption=None) -> None:
        self._check_open()
        self.children.append(Table(data, columns, caption))

    def metric(self, label, value, *, delta=None, unit=None) -> None:
        self._check_open()
        self.children.append(Metric(label, value, delta, unit))

    def add(self, prompt: Prompt) -> Prompt:
        self._add_prompt(prompt)
        return prompt


class Section(Container):
    def __init__(self, doc, parent, title, id, context, brief):
        super().__init__(doc, parent)
        self.title, self.context, self.brief = title, dict(context or {}), brief
        par = parent.nearest_section() if parent else None
        self.level = (par.level + 1) if par else 1
        if id:
            self.id = doc._claim(id, section=True)
        else:
            slug = slugify(title) or f"section-{doc._section_count + 1}"
            self.id = doc._claim(parent.expansion_prefix() + slug, dedupe=True, section=True)
        doc._section_count += 1

    def __enter__(self) -> "Section":
        self._token = _STACK.set(_STACK.get() + (self,))
        return self

    def __exit__(self, *exc) -> None:
        _STACK.reset(self._token)

    def __repr__(self) -> str:
        return f"<Section {self.id!r}>"


class Expansion(Node, Container):
    """FR-DYN-1: a point in the document that grows when `after` has run."""

    kind = "expansion"

    def __init__(self, doc, parent, build, after, id, max_nodes):
        Node.__init__(self, id)
        Container.__init__(self, doc, parent)
        self.build = build
        self.after = tuple(after) if isinstance(after, (tuple, list)) else (after,)
        self.max_nodes = max_nodes
        self.built = False
        self.created: list[Node] = []

    def deps(self) -> list[Node]:
        return list(self.after)

    def texts(self) -> list[str]:
        return []

    def depth(self) -> int:
        d, c = 1, self.parent
        while c is not None:
            if isinstance(c, Expansion):
                d += 1
            c = c.parent
        return d


# ---------------------------------------------------------------- document

class Document(Container):
    def __init__(self, title: str, *, metadata: dict | None = None, context: dict | None = None,
                 model: str | None = None, system: str | None = None, effort: str | None = None,
                 max_tokens: int | None = None, provider=None, style: str | None = None,
                 link_allowlist=(), purpose: str | None = None, coherence: str = "basic",
                 frame: bool | None = None, shared_brief: bool | None = None,
                 brief_instruction: str | None = None, flow: str | None = None,
                 max_flow_chars: int = 60_000, editor=None, repetition_threshold: int = 2):
        Container.__init__(self, self, None)
        self._seq = _next_seq()
        self.title, self.metadata = title, dict(metadata or {})
        self.context = dict(context or {})
        self.model, self.system, self.effort, self.max_tokens = model, system, effort, max_tokens
        self.provider, self.style, self.purpose = provider, style, purpose
        self.coherence = coherence
        self.frame = (coherence != "off") if frame is None else frame
        # Spike: shared_brief / editor / flow are accepted and recorded, not implemented.
        self.shared_brief, self.editor, self.flow = shared_brief, editor, flow
        self.attachments: dict[str, Any] = {}
        self._ids: set[str] = set()
        # SPIKE DEVIATION (see 0004 finding F1): sections get their own namespace by default,
        # because BRD §7 itself collides (section "Risks" -> `risks` vs Prompt(id="risks")).
        # Set doccode._core.SHARED_NAMESPACE = True for the BRD FR-DAG-1 behaviour.
        self._section_ids: set[str] = self._ids if SHARED_NAMESPACE else set()
        self._section_count = 0
        self._frozen = False
        self._prompt_counters: dict[str, int] = {}
        self._generated_ids: list[str] = []
        self._anon = 0
        self._expansion_count = 0

    # --- ids
    def _claim(self, id: str, dedupe: bool = False, *, section: bool = False) -> str:
        space = self._section_ids if section else self._ids
        if id in space:
            if not dedupe:
                raise DuplicateIdError(
                    f"ID {id!r} is already used in document {self.title!r}.", ids=(id,),
                    hint=("sections, prompts, values and expansions share one namespace "
                          "(FR-DAG-1); " if SHARED_NAMESPACE else "") + "pick another id=")
            n = 2
            while f"{id}-{n}" in space:
                n += 1
            id = f"{id}-{n}"
        space.add(id)
        return id

    def _assert_open(self, where: Container) -> None:
        if not self._frozen:
            return
        exp = _EXPANSION.get()
        c = where
        while c is not None:
            if c is exp:
                return
            c = c.parent
        raise DocumentFrozenError(
            f"Document {self.title!r} is frozen: execute() has started.",
            hint="only an expansion's build function may add to a running document; "
                 "use doc.copy() for a re-run with changes (not in the spike)")

    # --- authoring
    def section(self, title: str, *, id: str | None = None, context: dict | None = None,
                brief: str | None = None) -> Section:
        stack = _STACK.get()
        parent = stack[-1] if stack and stack[-1].doc is self else self
        parent._check_open()
        s = Section(self, parent, title, id, context, brief)
        parent.children.append(s)
        return s

    def text(self, markdown: str) -> None:
        stack = _STACK.get()
        target = stack[-1] if stack and stack[-1].doc is self else self
        Container.text(target, markdown)

    def set_context(self, **kwargs) -> None:
        self._assert_open(self)
        self.context.update(kwargs)

    def attach(self, name: str, value) -> None:
        self._assert_open(self)
        self.attachments[name] = value

    def expand(self, build: Callable, *, after, id: str | None = None,
               max_nodes: int = 50) -> Expansion:
        stack = _STACK.get()
        parent = stack[-1] if stack and stack[-1].doc is self else self
        parent._check_open()
        self._expansion_count += 1
        if not id:
            id = f"{parent.expansion_prefix()}_expand.{self._expansion_count}"
        exp = Expansion(self, parent, build, after, None, max_nodes)
        exp.id = self._claim(id)
        exp.doc = self
        parent.children.append(exp)
        return exp

    # --- running
    def plan(self, *, context: dict | None = None):
        from .execute import build_plan

        return build_plan(self, run_context=context or {})

    def execute(self, **opts):
        from .execute import execute_sync

        return execute_sync(self, **opts)

    async def aexecute(self, **opts):
        from .execute import aexecute

        return await aexecute(self, **opts)


@contextmanager
def _positioned(exp: Expansion):
    """Run code as if at the expansion point (FR-DYN-2)."""
    t1 = _STACK.set((exp,))
    t2 = _EXPANSION.set(exp)
    try:
        yield
    finally:
        _STACK.reset(t1)
        _EXPANSION.reset(t2)


def lookup(key: str) -> Node | None:
    return _REGISTRY.get(key)


__all__ = ["Document", "Section", "Prompt", "Value", "ctx", "configure", "string"]
