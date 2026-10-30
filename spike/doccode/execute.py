"""Graph build, validation, ready-queue execution and content assembly (BRD §4.3-4.6, §4.14).

Shortcuts (see spike/README.md): no cache, no lock, no budget, no retries, no events,
on_error="fail" only, checks are warn-only, no brief/editor/flow/repetition.
"""

from __future__ import annotations

import asyncio
import string
import threading
import time
import uuid
import warnings
from typing import Any

from . import _core
from ._core import (
    Document, Expansion, Metric, Node, Prompt, Section, Table, Text, Value,
    MISUSE_RE, TOKEN_RE, has_token, lookup, to_text,
)
from .clean import clean_output
from .content import (
    DocumentContent, ErrorBlock, MarkdownBlock, MetricBlock, PromptRecord, RunRecord,
    SectionContent, TableBlock,
)
from .errors import (
    ConfigError, CycleError, DocCodeError, ExecutionError, ExpansionError, ExpansionLimitError,
    NameConflictError, OrphanPromptWarning, TokenMisuseError, UnknownPlaceholderError,
    UnknownReferenceError,
)
from .providers import CompletionRequest, NoProvider

OUTPUT_INSTRUCTION = ("Write only the content requested, in Markdown. Do not add a preamble, "
                      "closing remarks or a top-level title; the document already provides "
                      "headings.")


# ================================================================ tree walking

def walk(container) -> list:
    """Every child item in document order, recursing into sections and expansions."""
    out = []
    for ch in container.children:
        out.append(ch)
        if isinstance(ch, (Section, Expansion)):
            out.extend(walk(ch))
    return out


def text_items(item) -> list[tuple[str, str]]:
    """(label, text) pairs scanned for tokens."""
    if isinstance(item, Prompt):
        return [("instruction", item.instruction)] + (
            [("system", item.system)] if item.system else [])
    if isinstance(item, Text):
        return [("text", item.markdown)]
    return []


def token_refs(text: str, owner_id: str) -> list[Node]:
    out = []
    for m in TOKEN_RE.finditer(text):
        if m.group("key"):
            node = lookup(m.group("key"))
            if node is None:
                raise UnknownReferenceError(
                    f"{owner_id} references a prompt or value that no longer exists.",
                    ids=(owner_id,))
            out.append(node)
    return out


# ================================================================ graph

class Graph:
    def __init__(self, doc: Document):
        self.doc = doc
        self.nodes: dict[str, Node] = {}
        self.deps: dict[str, set[str]] = {}
        self.order: dict[str, int] = {}

    def add_from(self, items: list, run_context: dict) -> list[Node]:
        """Add registered nodes in `items`, plus reachable helpers. Returns new nodes."""
        new: list[Node] = []
        work: list[Node] = []
        static_refs: list[tuple[str, Node]] = []
        for it in items:
            if isinstance(it, (Prompt, Expansion)):
                work.append(it)
            elif isinstance(it, Text):
                owner = f"static text in {getattr(it.container, 'id', 'preamble')}"
                validate_text(it.markdown, owner, "text", {}, context_chain(it, run_context))
                for n in token_refs(it.markdown, owner):
                    static_refs.append(("text", n))
            elif isinstance(it, Metric) and isinstance(it.value, Node):
                static_refs.append(("metric", it.value))
            elif isinstance(it, Table) and isinstance(it.data, Node):
                static_refs.append(("table", it.data))
        work.extend(n for _, n in static_refs)
        seen: set[int] = set()
        while work:
            n = work.pop(0)
            if id(n) in seen or (n.id in self.nodes and self.nodes[n.id] is n):
                continue
            seen.add(id(n))
            if n.doc is not None and n.doc is not self.doc:
                raise UnknownReferenceError(
                    f"{n.kind} {n.id} belongs to another document ({n.doc.title!r}).",
                    ids=(n.id,), hint="tokens are document-scoped (FR-REF-4)")
            if n.id is None:  # unregistered helper, FR-PRM-5
                self.doc._anon += 1
                n.id = self.doc._claim(f"_anon.{self.doc._anon}")
            elif n.doc is None and n._explicit_id:
                self.doc._claim(n.id)
            n.doc = self.doc
            self.nodes[n.id] = n
            new.append(n)
            deps: list[Node] = list(n.deps())
            for label, t in text_items(n):
                deps.extend(token_refs(t, n.id))
            for d in deps:
                work.append(d)
            self.deps[n.id] = {d.id for d in deps if d.id}  # fixed up below
        # dep ids may have been assigned after the dependent was processed
        for n in new:
            ds = list(n.deps())
            for _, t in text_items(n):
                ds.extend(token_refs(t, n.id))
            self.deps[n.id] = {d.id for d in ds}
        self.reorder()
        for n in new:
            self.validate_node(n, run_context)
        self.check_cycles()
        return new

    def reorder(self) -> None:
        pos = {id(x): i for i, x in enumerate(walk(self.doc))}
        for nid, n in self.nodes.items():
            # helpers (not in the tree) sort first so they start early
            self.order[nid] = pos.get(id(n), -1)

    # ------------------------------------------------ validation
    def validate_node(self, n: Node, run_context: dict) -> None:
        if not isinstance(n, Prompt):
            return
        nodes_in_ctx = [k for k, v in context_chain(n, run_context).items() if isinstance(v, Node)]
        if nodes_in_ctx:  # spike guard, finding F6: BRD would str() it into a raw token
            raise TokenMisuseError(
                f"Prompt {n.id}: context value(s) {nodes_in_ctx} are prompts/values.",
                ids=(n.id,), hint="context is inserted literally and adds no dependency; "
                                  "use refs={...} or an f-string reference instead")
        conflict = set(n.refs) & set(context_chain(n, run_context))
        if conflict:
            raise NameConflictError(
                f"Prompt {n.id}: name(s) {sorted(conflict)} defined in both refs= and context.",
                ids=(n.id,), hint="rename one of them (FR-REF-5)")
        for label, t in text_items(n):
            validate_text(t, n.id, label, n.refs, context_chain(n, run_context),
                          in_expansion=n.parent is not None and bool(
                              n.parent.expansion_prefix()))

    def check_cycles(self) -> None:
        state: dict[str, int] = {}
        path: list[str] = []

        def visit(u: str) -> None:
            state[u] = 1
            path.append(u)
            for v in sorted(self.deps.get(u, ())):
                if state.get(v) == 1:
                    cyc = path[path.index(v):] + [v]
                    raise CycleError("Dependency cycle: " + " → ".join(cyc), ids=tuple(cyc))
                if v not in state:
                    visit(v)
            path.pop()
            state[u] = 2

        for u in list(self.nodes):
            if u not in state:
                visit(u)


def validate_text(text: str, owner: str, label: str, refs: dict, context: dict,
                  in_expansion: bool = False) -> None:
    stripped = TOKEN_RE.sub("", text)
    m = MISUSE_RE.search(stripped)
    if m:
        frag = stripped[max(0, m.start() - 20): m.end() + 30]
        raise TokenMisuseError(
            f"{owner}: the {label} contains a broken reference token near {frag!r}.",
            ids=(owner,),
            hint="likely causes: f'{p!r}', a case change such as .upper(), slicing, or "
                 "str(prompt) used outside a prompt/text")
    if has_token(text):  # token mode, FR-REF-8
        for tm in TOKEN_RE.finditer(text):
            name = tm.group("name")
            if name and name not in context:
                raise UnknownPlaceholderError(
                    f"{owner}: ctx.{name} is not in the context.", ids=(owner,),
                    hint=f"set it with Document(context=...), doc.set_context({name}=...), "
                         f"section(context=...) or Prompt(context=...); known: "
                         f"{sorted(context) or 'none'}")
        return
    for _lit, field, spec, conv in string.Formatter().parse(text):
        if field is None:
            continue
        if not field.isidentifier():
            raise UnknownPlaceholderError(
                f"{owner}: placeholder {{{field}}} is not a plain name.", ids=(owner,),
                hint="placeholders are {name}; write literal braces as {{ }}")
        if field not in refs and field not in context:
            hint = ("if this is a Python variable holding a Prompt or Value, use an f-string "
                    f"(f\"...{{{field}}}...\") or refs={{'{field}': {field}}}")
            if in_expansion:
                hint += ("; inside an expansion, model text pasted into a plain string is "
                         "parsed for placeholders: pass it via context= instead (FR-DYN-2)")
            raise UnknownPlaceholderError(
                f"{owner}: unknown placeholder {{{field}}} in the {label}.", ids=(owner,),
                hint=hint)


# ================================================================ resolution

def context_chain(n, run_context: dict) -> dict:
    """FR-CTX-1 order, last wins."""
    doc: Document = n.doc
    out = dict(_core._CONFIG["context"])
    out.update(doc.context)
    container = getattr(n, "parent", None) or getattr(n, "container", None)
    if container is not None:
        for s in container.section_chain():
            out.update(s.context)
    out.update(getattr(n, "context", {}) or {})
    out.update(run_context)
    return out


def node_value_text(node: Node, max_chars: int | None = None) -> str:
    v = node._result if isinstance(node, Value) else node._result
    text = to_text(v)
    if max_chars and len(text) > max_chars:
        cut = text.rfind("\n\n", 0, max_chars)
        text = text[: cut if cut > 0 else max_chars]
    return text


def resolve(text: str, refs: dict, context: dict) -> str:
    """Single pass; inserted text is never scanned again (FR-REF-3)."""
    if has_token(text):
        def sub(m):
            if m.group("key"):
                n = m.group("n")
                return node_value_text(lookup(m.group("key")), int(n) if n else None)
            return to_text(context[m.group("name")])

        return TOKEN_RE.sub(sub, text)
    out = []
    for lit, field, _spec, _conv in string.Formatter().parse(text):
        out.append(lit)
        if field is not None:
            v = refs[field] if field in refs else context[field]
            out.append(node_value_text(v) if isinstance(v, Node) else to_text(v))
    return "".join(out)


# ================================================================ coherence frame (FR-COH-1)

def numbering(doc: Document) -> dict[int, str]:
    nums: dict[int, str] = {}

    def rec(container, prefix: str) -> None:
        i = 0
        for ch in container.children:
            if isinstance(ch, Section):
                i += 1
                nums[id(ch)] = f"{prefix}{i}"
                rec(ch, f"{prefix}{i}.")
            elif isinstance(ch, Expansion):
                # expansion children continue the parent's numbering
                for sub in ch.children:
                    if isinstance(sub, Section):
                        i += 1
                        nums[id(sub)] = f"{prefix}{i}"
                        rec(sub, f"{prefix}{i}.")

    rec(doc, "")
    return nums


def outline_frame(doc: Document, viewer: Node) -> str:
    lines = [f"Document: {doc.title}"]
    if doc.purpose:
        lines.append(f"Purpose: {doc.purpose}")
    lines.append("Outline:")
    inside = set()
    c = getattr(viewer, "parent", None)
    while c is not None:
        inside.add(id(c))
        c = c.parent

    def rec(container, depth: int) -> None:
        for ch in container.children:
            if isinstance(ch, Section):
                lines.append("  " * depth + f"- {ch.title}" + (f" ({ch.brief})" if ch.brief else ""))
                rec(ch, depth + 1)
            elif isinstance(ch, Expansion):
                if id(ch) in inside:
                    rec(ch, depth)
                else:
                    owner = container.title if isinstance(container, Section) else doc.title
                    lines.append("  " * depth + f"- {owner} — subsections generated at run time")

    rec(doc, 0)
    lines.append("Each section covers only its own scope; do not repeat material that belongs "
                 "to another section.")
    return "\n".join(lines)


def position_line(p: Prompt, nums: dict[int, str]) -> str | None:
    sec = p.parent.nearest_section() if p.parent else None
    if sec is None or not p.visible:
        return None
    brief = f" ({sec.brief})" if sec.brief else ""
    return f'You are writing section {nums.get(id(sec), "?")} "{sec.title}"{brief}.'


# ================================================================ executor

class Run:
    def __init__(self, doc: Document, provider, max_concurrency: int, run_context: dict):
        self.doc, self.provider = doc, provider
        self.sem = asyncio.Semaphore(max_concurrency)
        self.run_context = run_context
        self.graph = Graph(doc)
        self.records: dict[str, PromptRecord] = {}
        self.pending_prefixes: tuple[str, ...] = ()
        self.t0 = time.perf_counter()
        self.trace: list[tuple[str, str, float]] = []  # (event, node_id, t) for tests

    def provider_for(self, p: Prompt):
        return (p.provider or self.provider or self.doc.provider or _core._CONFIG["provider"]
                or NoProvider())

    async def run_prompt(self, p: Prompt) -> None:
        ctxd = context_chain(p, self.run_context)
        instruction = resolve(p.instruction, p.refs, ctxd)
        own_system = resolve(p.system, p.refs, ctxd) if p.system else None
        system = "\n\n".join(x for x in (
            None if p.raw else OUTPUT_INSTRUCTION, self.doc.style, self.doc.system) if x)
        prefix: list[tuple[str, str]] = []
        use_frame = self.doc.frame if p.frame is None else p.frame
        if use_frame:
            prefix.append(("frame", outline_frame(self.doc, p)))
        for name, val in self.doc.attachments.items():
            if p.attachments is None or name in p.attachments:
                prefix.append((name, f"<attachment name=\"{name}\">\n{to_text(val)}\n</attachment>"))
        parts = []
        if own_system:
            parts.append(f"Additional instructions for this part:\n{own_system}")
        pos = position_line(p, numbering(self.doc)) if use_frame else None
        if pos:
            parts.append(pos)
        parts.append(instruction)
        user = "\n\n".join(parts)
        sec = p.parent.nearest_section() if p.parent else None
        req = CompletionRequest(
            model=p.model or self.doc.model or _core._CONFIG["model"], system=system,
            prefix=tuple(prefix), messages=({"role": "user", "content": user},),
            effort=p.effort or self.doc.effort, max_tokens=p.max_tokens or self.doc.max_tokens,
            metadata={"node_id": p.id, "kind": "prompt", "section_id": sec.id if sec else None,
                      "instruction": instruction})
        prov = self.provider_for(p)
        started = time.perf_counter()
        comp = await prov.complete(req)
        level = sec.level if sec else 1
        text, notes = (comp.text, []) if p.raw else clean_output(
            comp.text, level, sec.title if sec else None)
        warn = list(notes)
        for chk in p.checks:  # spike: warn-only, no retry (FR-OUT-4 shortcut)
            srcs = tuple(s._result for s in getattr(chk, "sources", lambda: ())())
            reason = chk(text, srcs) if hasattr(chk, "sources") else chk(text)
            if reason:
                warn.append(f"check {getattr(chk, 'name', chk)} failed: {reason}")
        p._result, p._done = text, True
        self.records[p.id] = PromptRecord(
            id=p.id, kind="prompt", status="ok", provider=prov.name,
            resolved_instruction=user, system=system, prefix=tuple(prefix), raw_output=comp.text,
            input_tokens=comp.input_tokens, output_tokens=comp.output_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
            started_at=started - self.t0, ended_at=time.perf_counter() - self.t0,
            warnings=tuple(warn))

    async def run_value(self, v: Value) -> None:
        args = [d._result for d in v.deps()]
        v._result = await asyncio.to_thread(v.fn, *args)
        v._done = True
        self.records[v.id] = PromptRecord(id=v.id, kind="value", status="ok")

    async def run_expansion(self, e: Expansion) -> list[Node]:
        if e.depth() > 3:
            raise ExpansionLimitError(f"Expansion {e.id} is nested deeper than 3.", ids=(e.id,))
        args = [d._result for d in e.after]
        with _core._positioned(e):
            r = e.build(*args)
            if asyncio.iscoroutine(r):
                await r
        e.built = True
        items = walk(e)
        new = self.graph.add_from(items, self.run_context)
        e.created = new
        if len(new) > e.max_nodes:
            raise ExpansionLimitError(
                f"Expansion {e.id} created {len(new)} nodes (max_nodes={e.max_nodes}).",
                ids=(e.id,), hint="raise max_nodes= or cut the list in build()")
        check = getattr(self.provider_for_any(), "validate_ids", None)
        if check:
            check({n.id for n in new}, prefix=e.id)
        e._result, e._done = None, True
        self.records[e.id] = PromptRecord(id=e.id, kind="expansion", status="ok",
                                          warnings=(f"added {len(new)} nodes",))
        return new

    def provider_for_any(self):
        return self.provider or self.doc.provider or _core._CONFIG["provider"] or NoProvider()

    async def run_node(self, n: Node):
        async with self.sem:
            self.trace.append(("start", n.id, time.perf_counter() - self.t0))
            try:
                if isinstance(n, Prompt):
                    await self.run_prompt(n)
                elif isinstance(n, Value):
                    await self.run_value(n)
                elif isinstance(n, Expansion):
                    return await self.run_expansion(n)
            finally:
                self.trace.append(("end", n.id, time.perf_counter() - self.t0))
        return None

    async def go(self) -> None:
        g = self.graph
        g.add_from(walk(self.doc), self.run_context)
        for h in list(_core._HELPERS):  # FR-PRM-10
            if h.doc is None and h._seq > self.doc._seq:
                warnings.warn(OrphanPromptWarning(
                    f"Prompt {h._explicit_id or h.instruction[:40]!r} was created outside any "
                    f"section after {self.doc.title!r} was built and nothing references it, so "
                    "it will not run. Use section.add(prompt) or reference it."), stacklevel=4)
        exps = tuple(n.id for n in g.nodes.values() if isinstance(n, Expansion))
        check = getattr(self.provider_for_any(), "validate_ids", None)
        if check:
            check(set(g.nodes), pending_prefixes=exps)
        done: set[str] = set()
        started: set[str] = set()
        running: dict[asyncio.Task, Node] = {}
        while True:
            ready = [nid for nid in g.nodes if nid not in started and g.deps[nid] <= done]
            ready.sort(key=lambda nid: g.order.get(nid, 0))  # tie-break by document order
            for nid in ready:
                started.add(nid)
                running[asyncio.create_task(self.run_node(g.nodes[nid]))] = g.nodes[nid]
            if not running:
                break
            finished, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for t in finished:
                n = running.pop(t)
                exc = t.exception()
                if exc is not None:
                    for other in running:
                        other.cancel()
                    if isinstance(exc, (ConfigError,)) and not isinstance(n, Expansion):
                        raise exc
                    err = exc if not isinstance(n, Expansion) or isinstance(exc, DocCodeError) \
                        else ExpansionError(f"Expansion {n.id} build failed: {exc}", ids=(n.id,))
                    raise ExecutionError(
                        f"{n.kind} {n.id} failed: {err}", ids=(n.id,), cause=err,
                        run=self.record(),
                        outputs={k: v._result for k, v in g.nodes.items() if v._done}) from exc
                done.add(n.id)
                if isinstance(n, Expansion):
                    created = {c.id for c in n.created}
                    # FR-DYN-3: depends_on=<expansion> waits for everything it created
                    for nid, ds in g.deps.items():
                        if n.id in ds and nid not in created:
                            ds |= created

    def record(self) -> RunRecord:
        recs = tuple(self.records.values())
        return RunRecord(run_id=str(uuid.uuid4()), prompts=recs,
                         wall_ms=(time.perf_counter() - self.t0) * 1000,
                         input_tokens=sum(r.input_tokens for r in recs),
                         output_tokens=sum(r.output_tokens for r in recs))


# ================================================================ assembly

def table_block(t: Table) -> TableBlock:
    data = t.data._result if isinstance(t.data, Node) else t.data
    if hasattr(data, "to_dict"):
        data = data.to_dict("records")
    if isinstance(data, dict):
        keys = list(data)
        data = [dict(zip(keys, row)) for row in zip(*data.values())]
    cols = tuple(t.columns or (list(data[0]) if data else []))
    rows = tuple(tuple(str(r.get(c, "")) for c in cols) for r in data)
    return TableBlock(cols, rows, t.caption)


def assemble(doc: Document, run: Run) -> DocumentContent:
    def blocks_and_subs(container) -> tuple[list, list]:
        blocks, subs = [], []
        for ch in container.children:
            if isinstance(ch, Prompt):
                if ch.visible:
                    blocks.append(MarkdownBlock(ch._result, ch.id))
            elif isinstance(ch, Text):
                ctxd = context_chain(ch, run.run_context)
                blocks.append(MarkdownBlock(resolve(ch.markdown, {}, ctxd), None))
            elif isinstance(ch, Table):
                blocks.append(table_block(ch))
            elif isinstance(ch, Metric):
                v = ch.value._result if isinstance(ch.value, Node) else ch.value
                blocks.append(MetricBlock(ch.label, to_text(v), ch.delta, ch.unit))
            elif isinstance(ch, Section):
                b, s = blocks_and_subs(ch)
                subs.append(SectionContent(ch.id, ch.title, ch.level, tuple(b), tuple(s)))
            elif isinstance(ch, Expansion):
                b, s = blocks_and_subs(ch)
                blocks.extend(b)
                subs.extend(s)
        return blocks, subs

    pre, secs = blocks_and_subs(doc)
    return DocumentContent(title=doc.title, metadata=doc.metadata, preamble=tuple(pre),
                           sections=tuple(secs), run=run.record(), ok=True)


# ================================================================ entry points

async def aexecute(doc: Document, *, provider=None, max_concurrency: int = 4,
                   context: dict | None = None, on_error: str = "fail", lock=None, **ignored
                   ) -> DocumentContent:
    if on_error != "fail":
        raise ConfigError("on_error='continue' is not in the spike.", hint="see spike/README.md")
    if lock is not None:
        warnings.warn("lock= is accepted but ignored in the spike (no lockfile).", stacklevel=3)
    doc._frozen = True  # FR-DOC-9
    run = Run(doc, provider, max_concurrency, dict(context or {}))
    await run.go()
    content = assemble(doc, run)
    doc._last_run = run
    return content


def execute_sync(doc: Document, **opts) -> DocumentContent:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(aexecute(doc, **opts))
    # FR-EXE-1: inside a running loop (Jupyter) run on a worker thread
    box: dict[str, Any] = {}

    def worker():
        try:
            box["v"] = asyncio.run(aexecute(doc, **opts))
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    th = threading.Thread(target=worker)
    th.start()
    th.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


# ================================================================ plan (FR-DAG-3, cut down)

class Plan:
    def __init__(self, graph: Graph, generated: list[str]):
        self.graph, self.generated = graph, generated

    @property
    def levels(self) -> list[list[str]]:
        g, placed, out = self.graph, set(), []
        while len(placed) < len(g.nodes):
            lvl = sorted((n for n in g.nodes if n not in placed and g.deps[n] <= placed),
                         key=lambda n: g.order[n])
            out.append(lvl)
            placed |= set(lvl)
        return out

    def __str__(self) -> str:
        g = self.graph
        lines = [f"Plan for {g.doc.title!r}: {len(g.nodes)} nodes"]
        for i, lvl in enumerate(self.levels, 1):
            lines.append(f"level {i}:")
            for nid in lvl:
                n = g.nodes[nid]
                tag = n.kind
                if isinstance(n, Expansion):
                    tag += f", size unknown until run (max_nodes={n.max_nodes})"
                if nid.startswith("_anon") or (isinstance(n, Prompt) and not n.visible):
                    tag += ", helper"
                if nid in self.generated:
                    tag += ", generated id"
                deps = sorted(g.deps[nid])
                lines.append(f"  {nid} [{tag}]" + (f" <- {', '.join(deps)}" if deps else ""))
        return "\n".join(lines)

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        for nid, ds in self.graph.deps.items():
            for d in sorted(ds):
                lines.append(f'  "{d}" --> "{nid}"')
        return "\n".join(lines)


def build_plan(doc: Document, run_context: dict) -> Plan:
    g = Graph(doc)
    g.add_from(walk(doc), run_context)
    return Plan(g, list(doc._generated_ids) + [i for i in g.nodes if i.startswith("_anon")])
