# 0002: Package name — `doccode` is blocked by `doc-code`; publish as `doccode-py`, import `doccode`

**Status:** accepted by the owner, 2026-10-30: publish as **`doccode-py`**; import name and CLI command stay `doccode`. (Proposed by the Researcher, WP-000.)
**Requirements:** BRD §8 open question "Package name", BRD Phase 0, DEVELOPMENT_PLAN §0.6 (fallbacks in order: `doccode-py`, `docsascode`, `prosecode`; import name stays `doccode`)

## Problem

DocCode needs a PyPI distribution name. The import name is `doccode` regardless (§0.6). Is `doccode` free, and if not, which fallback should we use?

## Findings

### PyPI (checked 2026-10-30)

| Name | `https://pypi.org/pypi/<name>/json` | Separator-insensitive collision | Verdict |
| --- | --- | --- | --- |
| `doccode` | 404 (no project) | **`doc-code` exists** | **Blocked** in practice (see below) |
| `doccode-py` | 404 | none found | **Available** |
| `docsascode` | 404 | none found (`docs-as-code` is also 404) | Available, but see GitHub |
| `prosecode` | **200: taken** | — | **Taken**: "Literate programming in Python from markdown to LaTeX", v0.2.0, last upload 2020-04-03, https://pypi.org/project/prosecode/ |

**Why `doccode` is blocked although its JSON URL returns 404.** PyPI normalises names per PEP 503, so `doc-code` and `doccode` are different names and `pypi/doccode/json` is 404. But Warehouse, PyPI's server, also refuses to *create* a project whose "ultranormalized" name matches an existing project. It raises `ProjectNameUnavailableSimilarError`, which the user sees as "The name '…' is too similar to an existing project":

- Warehouse source, `warehouse/packaging/services.py`: `func.ultranormalize_name(Project.name) == func.ultranormalize_name(name)` → `raise ProjectNameUnavailableSimilarError(similar_project_name)` (https://github.com/pypi/warehouse/blob/main/warehouse/packaging/services.py, around line 606).
- Ultranormalisation removes `.`, `_` and `-` and lowercases the name before comparing: https://discuss.python.org/t/pypi-package-name-too-similar-to-an-existing-project/21696 and https://jslazak.com/reverse-engineering-package-name-validation-on-pypi. Real cases: https://github.com/pypi/support/issues/3267 (`deepcompare` blocked by `python-deepcompare`), https://github.com/pypi/support/issues/7510. I did not read the SQL definition of `ultranormalize_name` itself. The separator-stripping behaviour comes from these secondary sources and the support cases. Some sources say it also folds look-alike characters, which I have not verified.
- The existing project is **`doc-code`** 0.1.0, uploaded 2026-08-02: "Generate and safely apply AI-assisted source documentation" (https://pypi.org/project/doc-code/, homepage https://argolo.dev). Its wheel ships the import package `doc_code` and the console script `doc-code`, so there is **no import or CLI clash** with `doccode`. Only the distribution name is blocked.
- A scan of the full PyPI simple index (927,136 names) for ultranormalized matches found exactly `doccode ← doc-code` and `prosecode ← prosecode`. It found none for `doccodepy`, `docsascode`, `pydoccode` or `doccodelib`.
- The only certain test is an upload attempt. A PEP 541 transfer is unlikely to succeed, because `doc-code` is a new, live project and not abandoned (https://peps.python.org/pep-0541/).

### GitHub (obvious clashes)

- `doccode in:name`: 28 repositories, none significant. Top hits: `ksen145/DocCode` (Rust, generates docs from source code, 2 stars, active October 2026), `ger0nymo/doccode` (LLM code-doc generator, 0 stars). The account/org name `github.com/doccode` exists (one 2017 repo), so `doccode/doccode` is not available as a repo path. The owner picks the org anyway (BRD §1.7).
- `docsascode in:name`: 95 repositories. "Docs as code" is an established technical-writing term for managing documentation in Git (for example `codeandmedia/zola_docsascode_theme`, `docascod/DocsAsCode`). The name would be confused with that practice and is hard to search for.

## Options

1. **`doccode-py` on PyPI, `import doccode`, CLI `doccode`.** It is the first fallback in §0.6, it is free, and it has no similar-name collision. Distribution names that differ from the import name are common on PyPI. The cost is one line in the README: `pip install doccode-py`.
2. **Try `doccode` anyway**, or file a PEP 541 request. The upload will very likely fail, and PEP 541 does not apply to active projects.
3. **`docsascode`.** Free on PyPI, but it collides with the "docs as code" practice. Its search results would be swamped, and it suggests documentation tooling rather than LLM documents.
4. **`prosecode`.** Taken (2020, inactive). It would need a PEP 541 abandoned-project request, which takes months and is not guaranteed.

## Recommendation

Option 1: distribution name **`doccode-py`**, import package and CLI **`doccode`**. Per BRD Phase 0 this is a stop for the owner's choice. The other two alternatives, as the BRD asks, are `docsascode` (free, but ambiguous) and a fresh name such as `pydoccode` (free on PyPI with no ultranormalized collision, checked 2026-10-30).

## Consequences

- WP-001 sets `[project] name = "doccode-py"` in `pyproject.toml`, the console script `doccode`, and the package directory `src/doccode/`. No code imports change.
- The README, docs and the `RenderError` install hint (FR-RND-5) say `pip install "doccode-py[pdf]"`.
- Reserve the name early (owner): publish a 0.0.0 placeholder, or register a pending Trusted Publisher, so the name cannot be taken before 0.1.0.
- Re-check `doccode-py` immediately before the first upload. Availability can change at any time.
