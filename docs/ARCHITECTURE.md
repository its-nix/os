# Architecture

## Purpose

Nix is a six-human portfolio organization supported by seven role-isolated Hermes profiles. Human authority remains with Lior and the relevant human owners; profiles coordinate and execute bounded work but do not manufacture authority.

## Topology

```text
Lior ⇄ Discord (single shared bot identity)
          ├── #pulse ─────────────── Pulse Lead ───── Pulse board
          ├── #algua ─────────────── Algua Lead ───── Algua board
          ├── #meabeclick ────────── MeaBeclick Lead ─ MeaBeclick board
          │                              ├── UX Specialist
          │                              ├── Engineering Specialist
          │                              └── Growth Specialist
          └── other channels/DMs ─── LNX/default
```

The shared bot routes each project channel directly to its matching Lead; the portfolio channel routes to LNX. LNX is not a required intermediary for ordinary project work, decisions, status, or escalation. This changes the profile handling the channel, not the visible Discord account. Leads own their outcomes, decomposition, recovery, synthesis, and direct communication with Lior. Specialists report to their Lead and operate only within their stated duties.

## Planes and sources of truth

### Human communication plane

Discord carries requests, alerts, decision prompts, and live voice. It is not the canonical task ledger or evidence store. The three project channels are explicitly routed to their Leads, and the portfolio channel is routed to LNX. The bot's existing user allowlist remains unchanged; profile routing does not create new bot identities or grant new authority.

### Personal commitment plane

Todoist, through `td`, contains only commitments assigned to Lior. A lead must not mirror delegated cards into Todoist. When Lior must act, the orchestrator may create or request one clearly worded personal task.

### Delegated execution plane

Hermes Kanban is the durable work queue. Each project has one board and one persistent lead. Cards carry scope, assignee, dependencies, status, and completion evidence. Specialists communicate through board handoffs rather than extra messaging bots.

### Evidence plane

GitHub is canonical for code, issues, reviews, and CI. Project documents are canonical for approved decisions, operating notes, and deliverables that do not belong in source control. A chat claim is not durable evidence.

## Company knowledge proposal

[Knowledgebase integration](KNOWLEDGEBASE-INTEGRATION.md) proposes company-level retrieval and curation while linking rather than copying project technical truth. LNX curates portfolio context, Leads own project truth, and specialists receive scoped context through their Lead. The Markdown-only candidates and role-template pointers are inert, not deployed or permission-changing. Same-UID profiles are not confidentiality boundaries; real all-tool/principal/credential isolation and GitHub enforcement remain separately gated.

## Skill capability plane

Persistent third-party capability is declared in `skills/packs.json`, never installed globally. Baseline evidence verification is shared; named role packs are assigned explicitly to the seven profiles. Task-only and excluded skills remain outside persistent prompt context. A pinned, clean NixKit checkout supplies immutable profile-local copies through an exact subset command; the Nix OS wrapper is preview-first and never installs tools, authenticates accounts, or resolves conflicts by replacement.

Weekly discovery is a proposal plane, not an authority plane. Seven profile-local scouts write public-metadata packets to untracked local state. The LNX aggregator alone validates completeness, deduplicates, and may deliver one digest after canaries. Promotion still requires a profile-specific PR and Lior approval.

## Control flow

1. Lior sends project requests and decision responses directly to the responsible Lead in that project's Discord channel; portfolio-wide matters go to LNX.
2. The Lead owns the specification-level objective, acceptance criteria, project Kanban work, and verified outcome.
3. The Lead decomposes work and assigns specialists only when their contracts fit; specialists report to that Lead.
4. Workers attach verifiable artifacts or references and identify blockers. The Lead resumes on relevant events, recovers autonomously within granted authority, and proactively asks Lior directly only for a concrete human-only decision.
5. The Lead verifies exact-head evidence and reports the outcome directly to Lior. LNX independently monitors portfolio health and cross-project risks without controlling project task state or claiming a Lead's completion.

## Deliberate non-goals

The system has no external workflow engine, second team-chat plane, bespoke multi-agent runtime, or separate messaging bot for every internal role. Hermes profile routing provides direct lead conversations in the three named project channels; profiles and Kanban supply the required role separation and durable coordination.

## Trust boundary

Profiles isolate Hermes state but are not OS sandboxes. Role contracts constrain behavior; filesystem and credential isolation must be enforced by local deployment controls. Sensitive data must never enter Kanban card bodies, role files, this manifest, or source control.
