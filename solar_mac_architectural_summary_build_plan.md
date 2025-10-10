# Solar‑MAC (Solar MultiAgents Communications)

A shared, vendor‑neutral agent communications fabric that models your organization as a **solar system**: the **Sun** is the primary message store; **planets** are humans; each planet has **moons** (their agents). One moon per planet acts as the **Primary Coordinator**—aggregating sibling moons’ updates and reporting to the Sun.

---

## 1) Nomenclature & Metaphor

- **Sun** → Primary message store (Supabase Postgres + Realtime). Canonical log and pub/sub hub.
- **Planet** → A human (engineer, PM, designer).
- **Moon** → An agent owned by that human (Claude/GPT‑5/RAG bot/tool runner).
- **Primary Coordinator Moon** → One agent per human×project that subscribes to project streams, orchestrates sibling moons (tools/LLMs), and writes consolidated updates to the Sun.

---

## 2) Key Components

1. **Supabase (Postgres + Realtime) — the Sun**
   
   - Tables: `messages`, `progress`, `agent_meta`, `humans`, `projects` (and `user_project_memberships` if needed).
   - Realtime channels for project/human routing; **Row‑Level Security** for least‑privilege access.
   - JSONB envelopes to remain provider‑agnostic.

2. **Client Agents (Moons) — per human×project**
   
   - Small daemons (TS/Python) running locally or containerized.
   - Subscribe to Supabase (project inbox), run **LangGraph** workflows, expose/consume **MCP** tools, emit progress/telemetry.
   - One is designated **Primary Coordinator**: reads sibling moons’ signals (tests, lints, retrieval), aggregates, and writes summaries to the Sun.

3. **LangGraph Orchestrator**
   
   - Deterministic, resumable graphs for tasks (plan → tool calls → verification → PR updates).
   - Human‑in‑the‑loop gates; retries; idempotency.

4. **MCP Tool Servers (Local & Remote)**
   
   - Standardized tool access for IDEs and agents: GitHub/Jira/Vector DB/Browser/Repo Search/Tests.
   - Same servers are callable by Claude in VS Code and by server agents—single integration surface.

5. **Web App & Visualization**
   
   - Next.js frontend backed by Supabase subscriptions.
   - **Solar System UI**: planets sized by human activity/agent count; moons sized by agent activity; color encodes status; visible coordinator moon per planet.

6. **External Services**
   
   - GitHub (App or PAT) for PRs/Checks/comments; CI; Slack/Jira; S3/object storage; observability (OTel/Tempo).

---

## 3) Message Envelope (A2A v1)

A stable JSON envelope for all messages—tool‑agnostic and framework‑neutral:

```json
{
  "schema": "company.a2a.v1",
  "type": "task.create | task.update | chat | tool.request | tool.result | sys.event",
  "routing": {
    "project_id": "string",
    "from": "agent:<name> | human:<id>",
    "to": "agent:<name> | human:<id> | broadcast",
    "reply_to": "message_id | null"
  },
  "context": {
    "human_id": "string",
    "capabilities": ["repo.search", "test.run"],
    "refs": ["vscode://file/...#L10-40", "pr:842"],
    "mcp_tools": ["github", "jira", "vectorstore"]
  },
  "payload": {
    "text": "string",
    "attachments": [{"kind":"file|url|blob","uri":"..."}],
    "params": {"temperature": 0.3}
  },
  "status": "sent | received | processing | blocked | needs_human | done | error",
  "telemetry": { "model": "gpt-5", "latency_ms": 0, "cost_est_usd": 0.0 },
  "timestamps": { "created": "ISO", "updated": "ISO" },
  "id": "uuid"
}
```

**Progress rows** (linked to a message):

```json
{
  "schema": "company.progress.v1",
  "message_id": "uuid",
  "project_id": "string",
  "percent_done": 0,
  "state": "queued | running | done | error",
  "note": "string",
  "updated_at": "ISO"
}
```

---

## 4) Simplified Technology Diagram

```
                         ┌─────────────────────────────────┐
                         │           The Sun               │
                         │  Supabase Postgres + Realtime   │
                         │  (messages/progress/meta + RLS) │
                         └──────────────┬──────────────────┘
                                        │ pub/sub
                                        │
                   ┌────────────────────┼────────────────────┐
                   │                    │                    │
           ┌───────▼───────┐    ┌───────▼───────┐    ┌───────▼───────┐
           │  LangGraph    │    │   Web App     │    │  MCP Servers   │
           │ Orchestrator  │    │  (Solar UI)   │    │ (GitHub/Jira/  │
           │ (graphs)      │    │  Next.js      │    │  Vector/CI...) │
           └───────┬───────┘    └───────┬───────┘    └───────┬───────┘
                   │                    │                    │
         tool calls│            realtime │            tool APIs│
                   │                    │                    │
       ┌───────────▼───────────┐  ┌─────▼─────┐       ┌──────▼──────┐
       │ Planets (Humans)      │  │  IDE/VS   │       │   GitHub     │
       │  Chris, Bazen, ...    │  │  Code+MCP │       │  (Checks/PR) │
       └───────────┬───────────┘  └───────────┘       └──────────────┘
                   │
         ┌─────────▼─────────┐
         │  Moons (Agents)   │  ← per human×project client agents
         │  - Coordinator *  │     (subscribe, orchestrate, emit)
         │  - gpt5-reviewer  │
         │  - claude-coder   │
         │  - jira-sync ...  │
         └───────────────────┘
```

---

## 5) Data Model (DDL sketch)

Core tables; enable RLS and project scoping.

```sql
-- humans
create table humans (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  role text,
  email text,
  password text
);

-- projects
create table projects (
  id text primary key,
  name text not null
);

-- agent_meta
create table agent_meta (
  agent_name text primary key,
  human_id uuid references humans(id),
  project_id text references projects(id),
  model_used text,
  capabilities jsonb,
  heartbeat_interval_sec int,
  updated_at timestamptz default now()
);

-- messages
create table messages (
  id uuid primary key default gen_random_uuid(),
  created timestamptz default now(),
  updated timestamptz default now(),
  project_id text references projects(id) not null,
  human_id uuid references humans(id),
  agent_name text,
  model_used text,
  to_agent text,
  intent text,
  status text check (status in ('sent','received','processing','blocked','needs_human','done','error')),
  content jsonb not null,
  telemetry jsonb,
  reply_to uuid references messages(id)
);

-- progress
create table progress (
  id uuid primary key default gen_random_uuid(),
  message_id uuid references messages(id) on delete cascade,
  project_id text references projects(id) not null,
  percent_done int check (percent_done between 0 and 100),
  state text check (state in ('queued','running','done','error')),
  note text,
  updated_at timestamptz default now()
);
```

> **RLS**: enable on `messages` and `progress`; allow read/write if the JWT user is a member of the project. Keep a `user_project_memberships(user_id, project_id, role)` helper table.

---

## 6) Proposed Build Process

### **Phase 0 — Ignite the Sun & Two Moons (MVP)**

**Goal:** Stand up the primary message store; define the envelope; prove bidirectional I/O with two distinct agents.

1. **Create Supabase project**; set `messages`, `progress`, `agent_meta`, `humans`, `projects` as above.
2. **Define JSON envelope** (`company.a2a.v1`) and acceptance rules (status lifecycle, required routing fields).
3. **Enable Realtime** on `messages` & `progress`; add RLS policies for project scoping.
4. **Implement two minimal agents (moons):**
   - *Agent A (Coordinator)*: subscribes to `project_id`, acknowledges `task.create`, emits `task.update` every 30–60s, writes `done`.
   - *Agent B (Worker)*: reacts to `tool.request` (e.g., simple static analysis), returns `tool.result`.
5. **Wire LangGraph** in Agent A to call Agent B via Sun messages (no direct RPC), ensuring idempotent steps.
6. **Smoke tests:**
   - Publish a `task.create` → see both agents read via Realtime and write back `task.update`/`tool.result`.
   - Kill/restart an agent; verify it resumes (idempotent step anchoring by `message.id`).

**Exit criteria:**

- Two agents (different models or roles) reliably read/write the Sun.
- Coordinator aggregates Worker output and posts a final `done` message with telemetry.
- All records visible in the Solar UI list view (even if the 3D view is stubbed).

---

### **Phase 1 — Planets & MCP Tools**

- Add **client agent** per human×project; expose local MCP tools (repo.search, test.run, fs.read/write).
- Integrate **GitHub App** (Checks API + PR comments); normalize webhook → `sys.event` into `messages`.
- Expand the Solar UI (activity sizing, status colors, coordinator badges).

### **Phase 2 — Deterministic Graphs & Guardrails**

- Flesh out LangGraph nodes (plan → edits → validate → PR update).
- Add human gates, retry/backoff, and cost/latency telemetry.
- Introduce secrets management; tighten RLS policies; audit trails.

### **Phase 3 — Scale & Observability**

- Optional low‑latency relay (NATS/Redis) in front of Postgres; Supabase remains source of truth.
- OpenTelemetry traces per run; dashboards for cost, latency, success rate.
- Multi‑project roll‑out; role‑based access and cross‑team sharing policies.

---

## 7) Coordinator Moon Responsibilities

- Subscribe to `{project_id, human_id}` channels; fan‑in sibling moon outputs.
- Orchestrate via LangGraph steps; keep **idempotent** commit markers in payload.
- Emit `progress` heartbeat and human‑readable summaries; escalate `needs_human` on risk or ambiguity.

---

## 8) Testing & Acceptance (MVP set)

- **Connectivity:** agents receive messages within ≤1s (Realtime) and persist responses.
- **Durability:** messages survive agent restarts; reprocessing remains idempotent.
- **Auth:** an agent outside a project cannot read its rows (RLS verified).
- **Inter‑agent:** Coordinator → Worker round‑trip completes with `tool.result` and final `done`.

---

## 9) Recommended Stack Recap

- **Sun:** Supabase (Postgres + Realtime + RLS)
- **Moons:** Client agents (TS/Python) with LangGraph + MCP tools
- **Planets:** Humans in Web UI (Next.js) + IDE (VS Code + Claude MCP)
- **Ecosystem:** GitHub App, Jira/Slack, Vector DB, CI, OTel

> **Why this works:** Protocolized tools (MCP), deterministic orchestration (LangGraph), a durable pub/sub Sun (Supabase), and a clear solar metaphor keep the system portable, debuggable, and human‑centered.
