# 0005: Default model — `claude-sonnet-5-5`; current Messages API request shape

**Status:** proposed by the Researcher (WP-000), 2026-10-30. Confirmed or changed at G3 (decision 0007).
**Requirements:** FR-PRV-1, FR-PRV-2, FR-PRV-3 (`DEFAULT_MODEL`), FR-PRV-5, FR-PRV-6 (pricing table), FR-PRV-7 (cache prefix), FR-PRV-8 (refusals), FR-EXE-6, FR-EXE-7, FR-PRM-1, FR-PRM-7, BRD §10 item 7, EVAL-12, EVAL-20, EVAL-21

All facts below come from the official Anthropic docs (platform.claude.com), read on 2026-10-30, not from memory. Prices are USD per million tokens (MTok).

## Problem

The contract freeze (WP-003) needs a real value for `DEFAULT_MODEL`. `AnthropicProvider` (FR-PRV-2) and the eval `LLM` wrapper (EVAL-21) need the current request shape: effort, thinking, sampling parameters, caching, refusals and batches. `evals/config.toml` holds assumed IDs and prices that need verifying.

## Current models (Claude API)

Source: https://platform.claude.com/docs/en/about-claude/models/overview, plus the per-model pages https://platform.claude.com/docs/en/models/sonnet-5-5/overview, https://platform.claude.com/docs/en/models/haiku-5-5/overview and https://platform.claude.com/docs/en/models/opus-5-5/overview.

| | Claude Opus 5.5 | Claude Sonnet 5.5 | Claude Haiku 5.5 | (Claude Fable 5.1) |
| --- | --- | --- | --- | --- |
| API ID | `claude-opus-5-5` | `claude-sonnet-5-5` | `claude-haiku-5-5` | `claude-fable-5-1` |
| Docs description | "For long-running agentic coding and knowledge work" | "The best combination of speed and intelligence" | "For high-volume, latency-sensitive tasks such as classification, extraction, and routing" | "For demanding reasoning and long-horizon agentic work" |
| Input / output | $4 / $20 | $2 / $10 | $0.10 / $0.50 (prompts ≤100k tokens); $0.50 / $2.50 (>100k) | $10 / $50 |
| Thinking | Adaptive, **always on** | Adaptive (on by default) | Adaptive (on by default) | Adaptive, always on |
| Default effort | `medium` | `high` | `medium` | `high` |
| Context / max output | 1M / 128K | 1M / 128K | 1M / 128K | 1M / 128K |
| Released / retirement | — / not sooner than 2027-09-22 | 2026-09-28 / not sooner than 2027-09-28 | 2026-10-07 / not sooner than 2027-10-07 | — / not sooner than 2027-09-01 |

- "Every Claude model ID is a pinned snapshot, including the dateless IDs used from the 4.6 generation on." The alias row repeats the ID. So `claude-sonnet-5-5` is pinned, which matters for FR-PRV-1's moving-alias limitation and for FR-CCH/FR-LCK.
- The overview's own guidance: "If you're unsure which model to use, start with Claude Opus 5.5 for most workloads."

## Pricing

Source: https://platform.claude.com/docs/en/about-claude/pricing.

| Model | Base input | 5m cache write | 1h cache write | Cache hit/refresh | Output | Batch input | Batch output |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `claude-opus-5-5` | $4 | $5 | $8 | **$0.20** (0.05×) | $20 | $2 | $10 |
| `claude-sonnet-5-5` | $2 | $2.50 | $4 | **$0.10** (0.05×) | $10 | $1 | $5 |
| `claude-haiku-5-5` ≤100k-token prompt | $0.10 | $0.125 | $0.20 | $0.01 | $0.50 | $0.05 | $0.25 |
| `claude-haiku-5-5` >100k-token prompt | $0.50 | $0.625 | $1 | $0.05 | $2.50 | $0.25 | $1.25 |

- Multipliers: 5-minute write = 1.25× base input, 1-hour write = 2×. Reads are 0.1× by default, but **0.05× on Opus 5.5 and Sonnet 5.5** (0.025× on Fable 5.1/Mythos 5.1). "These multipliers stack with other pricing modifiers, including the Batch API discount and data residency."
- Batch API: "a 50 percent discount on both input and output tokens".
- Haiku 5.5 is priced per request by total prompt length, cache reads and writes included. A request over 100,000 tokens pays the higher row for all its tokens. FR-PRV-6's table must model this threshold, or mark Haiku 5.5 cost as approximate.
- `inference_geo: "us"` multiplies every category by 1.1× (4.6+ models). Default global routing uses standard prices.
- Thinking tokens "are billed as output tokens, even when the thinking text isn't returned to you, and they count toward `max_tokens`" (https://platform.claude.com/docs/en/build-with-claude/thinking).

## Request shape for `AnthropicProvider` (FR-PRV-2)

### Effort

Source: https://platform.claude.com/docs/en/build-with-claude/effort.

- Sent as **`output_config.effort`** on `client.messages.create(...)`. It is GA with no beta header, and with no value "the model's default level applies".
- Values: `low`, `medium`, `high`, `xhigh`, `max`. Opus 5.5, Sonnet 5.5 and Haiku 5.5 "support all five effort levels". This matches the FR-PRM-1 set.
- Defaults: `medium` on Opus 5.5 and Haiku 5.5, `high` on every other effort-capable model, Sonnet 5.5 included. "Setting `effort` to the model's default … produces exactly the same behavior as omitting the `effort` parameter entirely."
- Mapping `effort=None` to omitting the field is therefore correct (FR-PRV-2). But the effective level then depends on the model, which is another reason the eval harness always sends it (EVAL-21).

### Sampling parameters

Source: the "Good to know" section of https://platform.claude.com/docs/en/models/sonnet-5-5/overview, and item 2 of https://platform.claude.com/docs/en/models/opus-5-5/migration-guide.

- **Rejected.** Sonnet 5.5: "Setting `temperature`, `top_p`, or `top_k` returns a 400 error." Opus 5.5: "Omit `temperature`, `top_p`, and `top_k`", listed under settings where "the API returns a 400 error". Haiku 5.5: "Omit `temperature`, `top_p`, and `top_k`."
- This confirms FR-PRV-2 and EVAL-21.

### Thinking

Sources: https://platform.claude.com/docs/en/build-with-claude/thinking, the Opus 5.5 migration guide, and the effort page.

- **Adaptive thinking.** On Claude 5 and later models, "thinking is already on and needs no configuration". Omit `thinking`, or send `{"type": "adaptive"}`, which is equivalent.
- **Manual budgets are rejected.** `{"type": "enabled", "budget_tokens": N}` returns 400 on Opus 4.7 and later.
- **Disabling thinking:**
  - Opus 5.5 returns 400 for `{"type": "disabled"}` at every effort.
  - Sonnet 5.5 rejects `disabled`. Its lowest setting is `{"type": "between_tools"}`, at `high` effort or below.
  - Haiku 5.5 accepts `disabled` at `high` or below.
- **DocCode should send no `thinking` field.**
- `display` defaults to `"omitted"` on Claude 5+. **Responses still contain `thinking` blocks**, with an empty `thinking` field. `AnthropicProvider` must therefore build `Completion.text` from `type == "text"` blocks only, never from `content[0]`.
- **Thinking counts toward `max_tokens`** (FR-PRV-5 truncation, FR-EXE-7 projection). Size defaults with room for thinking.

### Other 400s that matter to DocCode

Source: the Opus 5.5 migration guide; Sonnet 5.5 "five breaking changes".

- **Assistant prefill is rejected** on Opus 4.6 and later. DocCode must not prefill.
- **Forced tool use** (`tool_choice` `any`/`tool`) is rejected on Opus 5.5 and Sonnet 5.5. Haiku 5.5 was not checked.
- **FR-PRM-7 must use structured outputs:** `output_config={"format": {...}}`. The older `output_format` is deprecated and needs a beta header. Forced tools are not an option.

### Prompt caching (FR-PRV-7)

Source: https://platform.claude.com/docs/en/build-with-claude/prompt-caching.

- **Breakpoints.** "You can define up to 4 cache breakpoints." Automatic (top-level) caching uses one slot, and 4 explicit breakpoints plus automatic caching return 400. DocCode's at most two explicit breakpoints is fine. It should not also enable top-level automatic caching.
- **Prefix order:** "`tools`, `system`, then `messages`". FR-PRV-7's layout (system first, then shared content blocks at the start of the first user message) matches.
- **Minimum cacheable prompt length.** **512 tokens for Opus 5.5, Sonnet 5.5, Haiku 5.5**, Fable 5.1 and Opus 5. Older models need 1,024 (Opus 4.8, Sonnet 5, Sonnet 4.6), 2,048 (Opus 4.7) or 4,096 (Opus 4.6, Opus 4.5, Haiku 4.5). "Shorter prompts cannot be cached, even if marked with `cache_control` … processed without caching, and no error is returned."
- **TTL.** `{"type": "ephemeral"}` gives 5 minutes; `{"type": "ephemeral", "ttl": "1h"}` gives 1 hour. Longer TTLs must come before shorter ones.
- **Usage fields:** `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens` (after the last breakpoint) and `cache_creation.{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}`. Total input = the sum of the first three. FR-PRV-6 cost must price 5m and 1h writes separately if both TTLs are ever used.
- **Concurrency:** "a cache entry only becomes available after the first response starts". This confirms FR-PRV-7's note on the first parallel wave.
- **Lookback:** at most 20 positions are checked per breakpoint, so place the breakpoint at the end of the static prefix, as FR-PRV-7 does.

### Refusals (FR-PRV-8)

Sources: https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons and https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback.

- **Response.** A refusal is HTTP 200 with `stop_reason: "refusal"` and `stop_details: {"type": "refusal", "category": …, "explanation": …}`. `content` may be empty.
- **Categories:** `"cyber"`, `"bio"`, `"frontier_llm"`, `"reasoning_extraction"`, `"general_harms"`, or `null`. "`null` is a normal, permanent value". `explanation` text "is not stable, so display it rather than parse it". `stop_details` is `null` for every other stop reason.
- **Partial output.** A refusal "can arrive before any output, or mid-stream after partial output … treat any partial output as incomplete and discard it". This matches FR-PRV-8.
- **Billing.** A refusal before any output is billed only for `bio`, `frontier_llm` and `reasoning_extraction`. Mid-stream refusals bill input plus the output streamed. So a refusal can cost money, and the `PromptRecord` cost should come from `usage` rather than be assumed 0.
- **Fallback is opt-in.** It needs a `fallbacks` request field (server-side fallback, beta), or client-side retry. Sending no `fallbacks` (FR-PRV-8, EVAL-21) therefore means no fallback. The response `model` field is what the served-model assertion checks.
- **Prompt advice for DocCode prompts and the brief.** Prompts that ask the model to write out its reasoning (a `<thinking>` section, a "reasoning" JSON field) trigger `reasoning_extraction` refusals. Built-in prompts (decision 0006) must not ask for that.
- **Other stop reasons:** `end_turn`, `max_tokens` (FR-PRV-5), `stop_sequence`, `tool_use`, `pause_turn`, `refusal`, `model_context_window_exceeded`. The last one should also be treated as truncation under FR-PRV-5. This is a small BRD gap; see the consequences.

### Message Batches (evals, EVAL-20)

Source: https://platform.claude.com/docs/en/build-with-claude/batch-processing.

- 50% off input and output, stacking with caching. Caching in batches is "best-effort", with typical hit rates of 30–98%.
- Limits: up to 100,000 requests or 256 MB per batch. Most batches finish within 1 hour, and a batch expires after 24 hours. Results are kept for 29 days.
- `max_tokens ≥ 1`.
- The docs suggest the 1-hour cache TTL for batches with shared context.

### Minimal request (Python SDK)

```python
client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=16000,                              # includes thinking tokens
    system=[{"type": "text", "text": DOC_SYSTEM}], # FR-PRV-7 item 1
    messages=[{"role": "user", "content": [
        {"type": "text", "text": FRAME},
        {"type": "text", "text": ATTACHMENTS},
        {"type": "text", "text": BRIEF,
         "cache_control": {"type": "ephemeral"}},  # last static shared block
        {"type": "text", "text": PER_PROMPT_TEXT},
    ]}],
    output_config={"effort": "medium"},            # omitted when effort is None
    # never: temperature, top_p, top_k, thinking, fallbacks, assistant prefill
)
```

## Options for `DEFAULT_MODEL`

1. **`claude-sonnet-5-5`.** $2/$10, "best combination of speed and intelligence", fast. Default effort `high`.
2. **`claude-opus-5-5`.** $4/$20, the docs' general starting recommendation. Default effort `medium`. Thinking can't be turned off.
3. **`claude-haiku-5-5`.** $0.10/$0.50, built for classification, extraction and routing rather than long-form prose.

## Recommendation

**Option 1: `DEFAULT_MODEL = "claude-sonnet-5-5"`.**

- **Cost.** A DocCode document is many calls: sections, brief, editor and expansions. Sonnet 5.5 costs exactly half of Opus 5.5 in every token category: input, output, cache writes and cache reads ($0.10 versus $0.20). That keeps the "cheap re-runs" goal (BRD §1.3 item 12) credible for a library default that users inherit without choosing.
- **Speed.** Sonnet 5.5 is "Fast" against Opus 5.5's "Moderate", which matters for `doccode run` feedback.
- **Eval consistency.** It keeps EVAL-12's "judge never weaker than the generator" strict (`claude-opus-5-5` judges `claude-sonnet-5-5`). It also matches the assumption already used in `docs/EVALS_PROPOSAL.md` and `evals/config.toml`.
- **Caveat.** Anthropic's overview recommends Opus 5.5 as the general starting point. The G0 ablation (WP-020) may compare both at fixed effort, and G3 (decision 0007) can switch the default on evidence. The switch is a one-constant change (FR-PRV-3).

`Prompt.effort=None` stays "provider default" (FR-PRV-1). The docs and examples recommend setting `effort` explicitly, because the default level differs by model (`high` on Sonnet 5.5, `medium` on Opus 5.5).

## Corrections needed in `evals/config.toml` (not edited here; WP-010 owns it)

| Key | Current | Correct | Source |
| --- | --- | --- | --- |
| `prices."claude-sonnet-5-5".cache_read` | `0.20` | **`0.10`** | Pricing page model table and footnote 2 ("0.05x … $0.10 USD on Claude Sonnet 5.5"); Sonnet 5.5 overview "Cache read $0.10 / MTok" |

Verified as correct, no change needed:

- `models.generator = "claude-sonnet-5-5"`, `models.judge = "claude-opus-5-5"` (valid pinned IDs).
- Sonnet `input 2.00`, `output 10.00`, `cache_write 2.50`.
- Opus `input 4.00`, `output 20.00`, `cache_read 0.20`, `cache_write 5.00`. The cache-write figures were guesses but are right: 5m write = 1.25× input.
- Effort `medium` is valid for both models.
- `max_tokens = 16000` is under the 128K limit.

Advisory, not errors:

- Add 1-hour write prices (`cache_write_1h`: Sonnet `4.00`, Opus `8.00`) if the judge batches use the 1-hour TTL the batch docs suggest. Otherwise keep the 5-minute TTL so the existing `cache_write` field stays the right rate.
- Batch prices are exactly 50% (Sonnet $1/$5, Opus $2/$10). The `use_batches` comment "about 50% cheaper" can say "50% cheaper". The cost projection (EVAL-20) should apply 0.5× to judge calls when `use_batches = true`.

## Consequences

- WP-003 sets `DEFAULT_MODEL = "claude-sonnet-5-5"` in `providers/anthropic.py`.
- WP-301/`providers/pricing.py` seeds the table above. It holds:
  - separate 5m and 1h write prices
  - the 0.05× read rate for Opus 5.5 and Sonnet 5.5
  - Haiku 5.5's 100k-token threshold, or Haiku 5.5 left out (cost `None`)
- `AnthropicProvider`:
  - sends no `thinking`, sampling parameters, `fallbacks` or prefill
  - reads text from `text` blocks only
  - reads `stop_details.category` for `refusal_category`
  - asserts `response.model == request.model`
  - uses one cache-minimum lookup (512 tokens for all three current models)
- Possible BRD clarification (orchestrator, not API-sensitive): also treat `stop_reason == "model_context_window_exceeded"` as truncated under FR-PRV-5.
- Possible BRD clarification for FR-PRM-7: specify `output_config.format` (structured outputs) and not forced tool use, which current models reject.
