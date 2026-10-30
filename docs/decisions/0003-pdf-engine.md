# 0003: PDF engine — keep WeasyPrint

**Status:** proposed by the Researcher (WP-000), 2026-10-30. The owner approves the PDF engine (DEVELOPMENT_PLAN §0.6).
**Requirements:** FR-RND-5, FR-RND-6, FR-RND-9, NFR-1, NFR-2 (`pdf` extra), NFR-8, NFR-15, NFR-22, NFR-24, BRD §9 risk "WeasyPrint system dependencies"

## Problem

FR-RND-5 converts the rendered HTML to PDF. The BRD names WeasyPrint, but Phase 0 asks for a comparison with Typst, whose PyPI package has no system dependencies. The comparison covers:

- install friction on the NFR-1 matrix
- typography
- `@page`-style headers and page numbers
- what a template designer must learn
- the restricted URL fetcher (NFR-22)

## Options

1. **WeasyPrint** (HTML/CSS to PDF), as specified.
2. **Typst** through the `typst` PyPI package. Templates become Typst markup instead of HTML/CSS.
3. **Others, briefly:** a headless Chromium (Playwright), xhtml2pdf, or wkhtmltopdf.

## Evidence

| | WeasyPrint 70.0 | Typst (`typst` 0.15.0) |
| --- | --- | --- |
| Licence | BSD 3-Clause (https://github.com/Kozea/WeasyPrint/blob/main/LICENSE) | Apache-2.0 (https://github.com/messense/typst-py) |
| Python | ≥3.10, matching NFR-1 (https://pypi.org/project/weasyprint/) | ≥3.8 |
| What pip installs | A pure-Python wheel (`weasyprint-70.0-py3-none-any.whl`) plus pure-Python deps (pydyf, cffi, tinyhtml5, tinycss2, cssselect2, Pyphen, Pillow, fonttools). Pango is loaded at run time through cffi. | Self-contained `abi3` wheels of about 29–36 MB for macOS x86_64/arm64, manylinux x86_64/aarch64/armv7/ppc64le/s390x, Windows amd64 and wasm32 (https://pypi.org/project/typst/#files). No system libraries. |
| System dependencies | **Pango ≥ 1.44** (https://doc.courtbouillon.org/weasyprint/stable/first_steps.html). Debian/Ubuntu: `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0` (plus `libharfbuzz0b` on Ubuntu). Alpine ≥3.17: the Pango/HarfBuzz/fontconfig libraries. macOS: `brew install weasyprint` or MacPorts Pango. **Windows: install MSYS2, then Pango in the UCRT64 shell, then pip; set `WEASYPRINT_DLL_DIRECTORIES` if DLLs aren't found.** Alternatively a standalone `.exe`, which the docs warn antivirus tools often flag as malware. | None. |
| CI (NFR-1 matrix) | Linux: one `apt-get` step. macOS: `brew install pango`. Windows: an MSYS2 setup step, or skip. NFR-8 already skips the PDF smoke test when WeasyPrint is missing, so the matrix stays green either way, but Windows PDF coverage costs an extra setup step. | `pip install` on every runner. |
| Template language | **HTML + CSS + Jinja2**, the same as the HTML renderer (FR-RND-6). A designer learns nothing new beyond CSS Paged Media. | **Typst markup and scripting.** It does not take HTML/CSS as input (the binding compiles `.typ` sources to PDF/PNG/SVG). Every template would be written twice, once as `document.html.j2` and once in Typst. Model output (Markdown) would need a second, Typst-escaping renderer path, beside the `nh3`-sanitised HTML path (FR-RND-4, NFR-15). |
| Headers and page numbers (FR-RND-9 `corporate`) | `@page` with `:left/:right/:first/:blank`, all page margin boxes, named pages, page-based counters ("with known limitations"), `string-set` running strings, running elements, `target-counter()`/`target-text()` for a TOC, and footnotes (https://doc.courtbouillon.org/weasyprint/stable/api_reference.html#supported-features). | Native and strong (`set page(header:…, numbering:…)`, `outline()`), with typography generally better than CSS engines. Measured comparisons were not run in this WP. |
| Layout limits | Flexbox and grid are "simple use cases" only. Multi-column pagination is "not seriously tested". Slow on long or crafted input (docs, Security section). | n/a (its own layout model). |
| Accessibility (NFR-24) | Tagged PDF (`--pdf-tags`) and PDF/UA-1 and PDF/A variants. The docs say output is "not guaranteed to be valid". | Not checked in this WP. |
| Restricted fetcher (NFR-22) | Supported directly. Subclass `weasyprint.urls.URLFetcher` (constructor: `timeout`, `ssl_context`, `http_headers`, **`allowed_protocols`**, `allow_redirects`, `fail_on_errors`) and override `fetch(url, headers=None) -> URLFetcherResponse`. Exceptions raised in `fetch` are caught and the resource is skipped, so refusing a URL means raising and logging a warning, and rendering goes on. `FatalURLFetchingError` stops rendering (https://doc.courtbouillon.org/weasyprint/stable/api_reference.html). The docs' Security section recommends exactly this: a custom fetcher that blocks `file://` or filters paths, plus process time and memory limits for untrusted input. | Different model: Typst reads only files under the project root, and paths that escape it fail (https://typst.app/docs/reference/foundations/path/). It has no general network access; `@preview` package imports are downloaded (informal: https://forum.typst.app/t/how-to-cross-reference-labels-defined-in-external-typst-projects/892/2). The binding also accepts a dict of file paths it reads from disk. A sandbox would need the root set to the template directory and package downloads disabled. I could not confirm from official docs that downloads can be disabled. |

**Others (brief).**

- **Headless Chromium through Playwright** has full modern CSS, and page margin boxes since Chrome 131 (https://developer.chrome.com/blog/new-in-chrome-131). But it downloads a browser binary of more than 100 MB, needs a subprocess and browser lifecycle, and is hard to sandbox to the NFR-22 rules (request interception would be needed). Too heavy for an optional extra.
- **wkhtmltopdf**: upstream repository archived 2023-01-02, with an old WebKit engine (https://github.com/wkhtmltopdf/wkhtmltopdf). Rejected.
- **xhtml2pdf**: pure Python on ReportLab, but supports only a limited CSS subset with its own `-pdf-` properties (https://xhtml2pdf.readthedocs.io/en/latest/reference.html). Too weak for the `corporate` template.

## Recommendation

**Option 1, WeasyPrint**, unchanged from the BRD.

- **One template language.** FR-RND-6 templates are HTML/CSS for both HTML and PDF. Typst would double every built-in template, add a second escaping path for model output and change what a designer must learn. That cuts against BRD §1 ("designers edit HTML/CSS templates").
- **NFR-22 fits WeasyPrint directly.** It has a documented, subclassable `URLFetcher` with `allowed_protocols`, which is what the requirement describes.
- **Install pain is real but contained.** It affects only the optional `pdf` extra. Markdown, HTML and JSON work everywhere. FR-RND-5's `RenderError` links to the install guide, and NFR-8 skips when WeasyPrint is missing.

Revisit Typst after 0.1.0 as a second PDF backend (an extra `pdf-typst`) if Windows install complaints dominate. Its no-system-dependency wheels are a real advantage.

## Consequences

- No BRD change. FR-RND-5/6/9 and NFR-8 stand as written.
- WP-402 implements `render/pdf.py` with a `URLFetcher` subclass:
  - `fetch()` allows `data:` URIs, plus `file:` paths that resolve (after `os.path.realpath`, so symlinks and `..` are caught) inside the active template directory.
  - Everything else raises an ordinary exception after logging a warning, so the resource is skipped and rendering continues. This includes `http(s):`, `ftp:`, other `file:` paths and special files such as `/dev/urandom`.
  - Also set `allowed_protocols={"data", "file"}` as a second layer.
- WP-402 tests (NFR-22): `<img src="http://…">` and `<img src="file:///etc/passwd">` are neither fetched nor embedded. Assert that the fetcher was called and refused, and that the PDF has no image XObject.
- WP-001 CI:
  - Linux installs the Pango packages for the PDF job.
  - macOS uses `brew install pango`.
  - Windows either adds an MSYS2 step or lets NFR-8 skip. Recommend skip for 0.1.0 and document it.
- README install section: link https://doc.courtbouillon.org/weasyprint/stable/first_steps.html and give the per-OS one-liners above.
- Pin `weasyprint>=…` in the `pdf` extra at the version WP-402 tests against. The `URLFetcher` class API documented above is from 70.0. Check the changelog before setting a lower bound, because older releases used a function-style `url_fetcher` (not verified here).
