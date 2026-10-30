# 0008: Orchestration engine — DocCode's own scheduler, not LangGraph

**Status:** accepted by the owner, 2026-10-30
**Requirements:** NFR-2, NFR-3, FR-EXE-3, FR-PRV-7, FR-CCH-*, FR-LCK-*, BRD §1.4

## Problem

DocCode runs a document as a DAG of prompts, values and expansions. LangGraph is a popular library for running LLM graphs. Should DocCode build on it?

## Options

1. **LangGraph as the engine.** Mature graph runtime, checkpointing, a large ecosystem.
2. **DocCode's own ready-queue scheduler** (FR-EXE-3), about a few hundred lines of `asyncio`.
3. **Own scheduler now, a LangGraph integration adapter later.**

## Decision

Option 3. The engine is DocCode's own scheduler; a LangGraph adapter is a roadmap item after 0.1.0 (BRD §11).

## Why

- **Dependencies.** NFR-2 keeps the core dependency set small, and NFR-3 keeps the SDK behind the `Provider` protocol. LangGraph pulls in the LangChain core stack and its own model abstractions.
- **Control of the request bytes.** Prompt caching (FR-PRV-7), the static provider fingerprint, the cache key and the lockfile all depend on DocCode building every request byte for byte. A framework layer between DocCode and the SDK would make those guarantees depend on another project's internals.
- **The graph is simple.** DocCode's graph is a static DAG plus expansions that add nodes at run time. It has no cycles, agent loops or human-in-the-loop pauses, which is where LangGraph earns its weight.
- **Determinism and testing.** Ties broken by document order, `max_concurrency=1` runs that are deterministic, and offline tests with `MockProvider` are easier to guarantee in a scheduler we own.
- **Non-goals.** BRD §1.4 excludes agent frameworks and tool-use loops from 0.1.0.

## Consequences

- WP-208a–d build the scheduler, as planned.
- Users who already use LangGraph get, after 0.1.0, an adapter that runs a DocCode document as one LangGraph node, or exposes LangGraph steps as `Value` nodes.
- Revisit if DocCode ever needs cycles, long-running pauses or durable checkpoints beyond the cache and lock.
