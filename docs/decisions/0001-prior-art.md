# 0001: Prior art — no near-duplicate found; continue

**Status:** proposed by the Researcher (WP-000), 2026-10-30; awaiting owner review
**Requirements:** BRD Phase 0 (prior-art check), BRD §1.2 positioning, §1.4 non-goals, §9 risk "a similar library shipped since March 2026", DEVELOPMENT_PLAN §7 (WP-000 hard gate)

> **Stop-and-report gate: PASSED. No near-duplicate found.** No maintained library was found that does most of DocCode's core: a document defined in Python as sections holding LLM prompts, run as a dependency DAG, with a design-agnostic content model rendered through swappable templates. The closest projects each cover one slice (template-filling, research agents, Python DAGs, publishing). Searches were run on 2026-10-30 and are listed in the evidence below; a search cannot prove absence, so the owner may want one more manual look at GitHub before G0.

## Problem

BRD §9 lists duplicate effort as the first risk, and Phase 0 requires checking PyPI and GitHub for libraries that build documents as code with embedded LLM prompts. That check comes before any signature is frozen. We need to know (a) whether a near-duplicate exists, which would stop the build, and (b) what to borrow from neighbours.

## Options

1. **Stop**, because a maintained near-duplicate exists, and contribute to it or wrap it.
2. **Continue as specified**, borrowing proven ideas from neighbours.
3. **Continue, but build on a neighbour's runtime** (Hamilton, Prefect or LangGraph as the engine).

## Recommendation

Option 2. Option 3 was already rejected for LangGraph in decision 0008. The same reasons (NFR-2 dependency budget, owning the request bytes for FR-PRV-7 and FR-CCH, determinism) apply to Hamilton and Prefect too.

## Evidence

Package data comes from the PyPI JSON API (`https://pypi.org/pypi/<name>/json`), fetched 2026-10-30. "Last release" is the newest upload time.

### Named in the BRD

| Project | What it does | Overlap with DocCode | What to borrow |
| --- | --- | --- | --- |
| **DSPy** (`dspy` 3.4.1, MIT, last release 2026-10-12) — https://dspy.ai, https://pypi.org/project/dspy/ | Programs LMs through declarative *signatures* and composable *modules*, and runs *optimizers* that tune prompts and few-shot examples against a metric. | Prompts as Python objects, composed in code. It has no document model, no sections, no rendering, and no lockfile. Optimisation is its focus. | Signatures as typed I/O contracts are a model for FR-PRM-7 (`output_schema`). Optimise against an eval metric, which is DocCode's §5.7 eval-first stance. Do not take the optimiser into 0.1.0. |
| **Instructor** (`instructor` 1.17.0, MIT, 2026-09-09) — https://python.useinstructor.com, https://pypi.org/project/instructor/ | Structured outputs: validates LLM responses against Pydantic models, with retries on validation failure. | Only FR-PRM-7 (structured output behind the `schema` extra). | Validate, then retry with the validation error fed back. This matches FR-OUT-4 checks with `on_check_fail`. Keep Pydantic optional, as NFR-2 already does. |
| **Mirascope** (`mirascope` 2.8.2, MIT, 2026-10-22) — https://mirascope.com, https://pypi.org/project/mirascope/ | "Every frontier LLM. One unified interface." Provider-agnostic calls through decorated Python functions, with structured output and tools. | The `Provider` protocol (FR-PRV-1) and prompts-as-functions ergonomics. No document, DAG or render layer. | Provider-neutral request and response types. Keep the provider seam thin (FR-PRV-1, NFR-3). |
| **Hamilton** (`sf-hamilton` 1.90.0, Apache-2.0, 2026-04-25; now "moved to `apache-hamilton`") — https://github.com/apache/hamilton, https://pypi.org/project/sf-hamilton/ | Builds a dataflow DAG from plain Python functions: a parameter name is a dependency on the function of that name. Has caching, lineage and visualisation. | DAG from Python with lineage, like FR-DAG and FR-EXE-9 provenance. It is generic dataflow with no document, prompt or template concepts. | Graph visualisation in `plan()`. Content-addressed caching keyed on inputs and code, as FR-CCH-1 already does. Lineage records. |
| **Prefect** (`prefect` 3.9.0, Apache-2.0, 2026-10-30) — https://www.prefect.io, https://pypi.org/project/prefect/ | Workflow orchestration: `@flow`/`@task`, retries, caching with cache keys, a scheduler, and a UI/server. | Retries with backoff (FR-EXE-6) and task caching (FR-CCH). It is a general orchestrator, far heavier than NFR-2 allows. | Retry policy shape (count, backoff, jitter). A cache key computed from inputs. Prefect's sibling projects Marvin/ControlFlow show that an orchestrator plus LLM still lacks a document layer. |
| **Quarto** — https://quarto.org (CLI, not a Python library; the `quarto` PyPI package is a 2021 0.1.0 shim) | Pandoc-based publishing. Executes code cells in Markdown/notebooks and renders HTML, PDF (LaTeX or Typst) and DOCX through themes. | Reproducible documents, and content kept apart from presentation. No LLM orchestration. The author writes the prose. | Executing code to produce a document is a familiar idea. Note its Typst PDF path (see decision 0003). Its project and theme layout is a model for FR-RND-6 template directories. |
| **Jupyter Book** (`jupyter-book` 2.1.7, 2026-09-23) — https://jupyterbook.org, https://pypi.org/project/jupyter-book/ | "Create computational narratives that are reusable, reproducible, and interactive." Version 2 is built on MyST. | Reproducible documents from code and Markdown. No LLM generation. | Cross-references and a table of contents as structure (FR-RND-9 `corporate`). |
| **LangChain / LangGraph** (`langchain` 1.4.7, `langgraph` 1.2.18, MIT, both October 2026) — https://www.langchain.com/langgraph | General LLM application framework, and a stateful graph runtime with cycles, checkpoints and human-in-the-loop. | Graph execution of LLM steps. Explicitly not a competitor (BRD §1.4). Already assessed in decision 0008. | Adapter after 0.1.0 (decision 0008, BRD §11). |

### Closer candidates found by searching

| Project | What it does | Why it is not a near-duplicate | What to borrow |
| --- | --- | --- | --- |
| **Genji** (`genji` 1.0.1, Apache-2.0, first release 2026-02-07, last 2026-03-25) — https://github.com/calebevans/genji | Jinja2 templates with a `gen("…")` function. The template owns structure (JSON, HTML, YAML); the LLM fills content slots, and independent `gen()` calls run in parallel through LiteLLM. | **The nearest match on "templates plus LLM", but the inverse design.** The template is the program, and prompts live inside Jinja, so content generation and design are mixed. BRD §1 separates them. It has no prompt-to-prompt references or DAG, no sections or content model, no provenance, cache-key lockfile or PDF. It sends `temperature`, which current Claude models reject. Two releases, quiet since March 2026. | Its pitch, "template owns structure, LLM fills content", supports FR-CNT-3: data is rendered, not retyped by the model. Format-aware escaping filters, a sanitisation idea already in FR-RND-4/NFR-15. |
| **GPT Researcher** (`gpt-researcher` 0.16.1, MIT, 2026-09-26) — https://docs.gptr.dev | Autonomous research agent. Plans subtopics, searches the web and writes a report (`research_report`, `resource_report`, `outline_report`). | The agent chooses the structure, and users steer it only through `custom_prompt`. It has web search and tools (BRD §1.4 non-goals) and no developer-defined sections or dependency graph. | Generating an outline, then sections from it, is the FR-DYN expansion use case. Its draft-section-titles step is close to an outline expansion. |
| **STORM / Co-STORM** (`knowledge-storm` 1.1.1, MIT, last release 2025-09-29) — https://github.com/stanford-oval/storm | Research engine: web research, then an outline, then a Wikipedia-style article with citations. | The pipeline generates the outline; users don't define it in code. It is research-first, with no templates or rendering layer. Last dated news is January 2025. | The writing-from-outline stage supports the FR-COH outline frame. Every section knows the full outline. |
| **PageForge** (`pageforge` 0.1.0, MIT, single release 2025-07-28) — https://github.com/iCreature/pageforge | Builds PDFs from Python dataclasses (header, paragraph, table, list sections) through ReportLab or WeasyPrint. Meant to render LLM-produced JSON. | Rendering only, with no LLM calls. One release. | Confirms the content-model-to-PDF split. Nothing to depend on. |
| **DocETL** (`docetl` 0.3.0, MIT, 2026-06-17) — https://github.com/ucbepic/docetl | LLM-powered ETL over document collections (map, reduce, resolve operators), declared in YAML or Python. | Processes many input documents into data. It doesn't author one output document. YAML-first, which BRD §1.4 excludes as the primary format. | Operator-level cost reporting and optimisation, compared with FR-EXE-7 budgets. |
| **FFAI** (prompt execution graph) — https://ffai.readthedocs.io/en/latest/api/core.graph.html | A `PromptNode` with dependency sequence numbers, run when dependencies complete, and cycle detection. | Prompt DAG only, with no documents, rendering, caching or provenance described. Small project. | Nothing beyond what FR-DAG already specifies. |
| **Burr** (`burr` 0.42.0, Apache-2.0; moved to `apache-burr`) — https://github.com/apache/burr | State-machine framework for LLM apps, with tracking UI and persistence. | Agent and app state machines (decision 0008 reasoning). | Tracking and replay UI as a possible post-0.1.0 viewer for `RunRecord`. |
| **signalwire-pom** (`signalwire-pom` 2.7.2, MIT, 2025-06-26) — https://pypi.org/project/signalwire-pom/ | "Prompt Object Model": a prompt as a tree of nested sections, rendered to Markdown, JSON or XML. | Structures one *prompt*, not a document. | Nothing. |
| **Others checked and set aside** — Haystack (`haystack-ai` 3.4.1), LlamaIndex (`llama-index-core` 0.14.25), Microsoft Prompt flow (`promptflow` 1.18.5), magentic 0.41.1, Marvin 3.2.10, ell 0.0.17 (inactive since Feb 2025), Outlines 1.3.3, Guidance 0.3.1, BAML (`baml-py` 0.226.3), Pydantic AI 2.63.0, papermill 2.7.0 | LLM app frameworks, prompt-as-function libraries, constrained decoding, DSLs, notebook parameterisation. | None has a document-shaped graph plus template rendering. | magentic and ell: prompt as a decorated function is a possible spike variant (WP-002). papermill: parameterised re-runs of one artefact. |

### Searches run (2026-10-30)

- Web: "Python library define document sections prompts LLM generate report DAG templates PDF documents as code"; "open source python LLM report generator multi-section outline sections parallel render HTML PDF Jinja templates library 2026"; "python library lazy prompt references DAG LLM document generation sections lockfile OR provenance report as code"; "report generation framework python LLM each section prompt depends on previous section outputs parallel asyncio library github 2026"; "python prompt DAG library prompts as nodes dependencies cached deterministic prompt graph open source". These turned up Genji, PageForge, FFAI, prompt-vcs (Git-native prompt versioning with a lockfile for *prompts*, not outputs), dynaprompt, TFrameX and AsyncLLM. None had a document model.
- PyPI: the full simple index (927,136 project names) was scanned for names containing `doccode`, `docsascode`, `docascode`, `documentsascode`, `prosecode`, `docforge`, `docdag` and `promptdoc`. The hits that matter are the existing `doc-code` and `prosecode` projects (see decision 0002), plus a `docforge-*` family that is not relevant.
- GitHub repository search `doccode in:name`: 28 repos, all small. The most relevant are `ksen145/DocCode` (Rust; generates documentation *from source code*, 2 stars) and `ger0nymo/doccode` (LLM code-documentation generator, 0 stars, 2024). These are code-documentation tools, not document-as-code generators.

## Consequences

- WP-001 and WP-002 may start. The positioning in BRD §1.2 stands. The defensible combination (a document-shaped DAG, a lockfile of generated text, data rendered rather than retyped, a design layer kept out of Python) was not found elsewhere.
- Borrow list for later WPs:
  - FR-OUT-4 retry-with-feedback, in the style of Instructor.
  - `plan()` graph visualisation, in the style of Hamilton.
  - Retry policy shape, in the style of Prefect.
  - Outline-to-sections expansion examples, in the style of STORM and GPT Researcher.
  - Template project layout, in the style of Quarto.
- Watch-list: re-run this search before 0.1.0 (Wave 5), with Genji and any new "documents as code" or LLM-report projects as the specific things to recheck.
