# 🤖 Autonomous AI Software Engineer

![tests](https://github.com/shubhampandit1836-glitch/ai-software-engineer/actions/workflows/tests.yml/badge.svg)

An autonomous coding agent that **plans, writes, security-scans, executes, and debugs** Python programs — with layered guardrails, a bounded self-repair loop, and a Claude-style streaming UI.

🎥 **[Watch the 60-second demo](YOUR_VIDEO_LINK)**

**Eval score: 9/9 (100%)** — [full report](backend/evals/results.json) · run it yourself: `uv run python -m evals.run_evals`

## What it does

| You ask | What the agent does |
|---|---|
| *"program to swap 2 numbers"* | Plans → writes code → static security scan → executes in a cloud sandbox → verifies output → **fixes itself if broken** (≤3 attempts) → clean final answer with code + verified output |
| *"what can you do"* | Classified as a general question → answered directly. No code, no sandbox |
| *unsafe request* | Blocked by a deterministic guardrail **before any LLM call** — zero tokens spent |

## Architecture

```text
START → [input_guardrail] ──(rejected)──────────────────────────→ END
              │ (clean)
              ▼
      [intent_classifier] ──(unsafe → END)──(general → direct_answer → END)
              │ (coding_task)
              ▼
          [planner] → [coder] → [security_scanner] ──(violation)→ [reviewer] ─┐
                          ▲              │ (clean)                            │
                          │              ▼                                    │
                          │          [tester] ──(failed)→ [reviewer] ─────────┤
                          │              │ (passed, more steps)               │
                          └──────────────┘                                    │
                                         │ (passed, done)   (3 attempts used) │
                                         ▼                   ▼                │
                                  [synthesizer]       [failure_answer] <──────┘
                                         └──────────→ END ←──────────┘
```

## Safety — three defensive layers

| Layer | Mechanism | Cost |
|---|---|---|
| 1. Input guardrail | Deterministic regex blacklist (malware, keyloggers, DDoS, destructive ops) | Zero tokens, instant |
| 2. Intent classifier | Fast LLM classifies: coding task / general question / unsafe | ~500 tokens |
| 3. Code security scanner | Static analysis pre-execution: blocks `subprocess`, `eval`, `os.system`, destructive deletes, infinite loops | Zero tokens |

Fixed code re-enters through the **scanner**, not the tester — so a repair can never introduce a new dangerous operation.

## Self-repair loop

`coder → scanner → tester`, and on failure `reviewer` debugs with the full error report, then the fixed code goes back through the scanner. A **3-attempt budget** per plan step prevents infinite loops; exhaustion exits gracefully through a structured failure answer — the agent never crashes, never hangs, never burns unbounded tokens.

## Key engineering decisions

- **Two-tier models:** fast model (intent, guardrail LLM, synthesis) vs. smart model (planning, coding, debugging) — right-sized inference spend.
- **Retry with exponential backoff** on provider 429 rate limits, plus a `max_tokens` cap under the free-tier OTPM ceiling — the agent survives rate limiting instead of crashing mid-run.
- **`asyncio.to_thread`** wraps the synchronous E2B SDK so the FastAPI event loop never blocks.
- **SSE buffer parsing:** network chunks don't respect event boundaries — the frontend holds incomplete JSON in a buffer until events are whole.
- **Event-merge accumulation:** LangGraph's `updates` mode can bundle multiple nodes per event; the frontend merges every non-null field into a snapshot, making the UI immune to stream-shape quirks.
- **Zero-dependency markdown renderer:** a line-by-line fence state-machine (not blank-line splitting — code contains blank lines) renders code blocks, verified output, and bold text with no `react-markdown` dependency.
- **Deterministic state factory** (`get_initial_state`) guarantees all 13 state fields exist from step one — no `KeyError`s mid-run.

## Eval results

```
TOTAL: 9/9 passed (100.0%)
  guardrail  3/3   (blocked with message, 0.0s, zero LLM calls)
  general    3/3   (answered conversationally, no code leakage)
  coding     3/3   (code + verified sandbox output)
```

*Agents are nondeterministic — scores are from repeated runs of the suite. See [`backend/evals/results.json`](backend/evals/results.json).*

## Tech stack

**LangGraph** (cyclic state machine, conditional routing) · **Groq** — qwen/qwen3.8-27b + openai/gpt-oss-20b (free tier) · **E2B** cloud sandboxes · **FastAPI** + Server-Sent Events · **React + Vite + Tailwind CSS**

## Project structure

```text
backend/
├── app/
│   ├── agent/      # state, nodes, graph, safety layers, answer nodes
│   ├── api/        # FastAPI SSE routes
│   ├── core/       # LLM factory + retry logic
│   └── tools/      # E2B sandbox executor
├── evals/          # eval harness + results.json
└── requirements.txt
frontend/src/
├── api/            # SSE stream client (async generator)
├── components/     # chat UI, collapsible agent trace, final answer card
├── hooks/          # useAgentStream (event accumulation)
└── utils/          # phase map, markdown renderer
```

## Getting started

**Prerequisites:** Python 3.11+, Node 18+, keys from [Groq](https://console.groq.com) and [E2B](https://e2b.dev)

**Backend:**
```bash
cd backend
uv venv && uv pip install -r requirements.txt
# create .env:  GROQ_API_KEY=...   E2B_API_KEY=...
uv run python -m uvicorn app.main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

## Known limitations (honest ones)

- **Niche factual questions can hallucinate** — general answers come from model knowledge with no retrieval. (Known failure: expanding niche acronyms.) Roadmap: web-search tool for the general path.
- **No multi-turn memory** — each message starts a fresh graph run. Roadmap: conversation history via LangGraph checkpointer.
- **Scope: small single-file Python programs** — by design. Multi-file projects need persistent sandboxes + file tools (roadmap v3).

## Roadmap

v3: persistent E2B sandbox + file tools → multi-file projects (e.g., "build a FastAPI app with tests") · retrieval for general questions · conversation memory · polyglot execution via shell tool · convert evals to pytest
