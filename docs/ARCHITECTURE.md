# Architecture

## Purpose

Nix is a six-human portfolio organization supported by seven role-isolated Hermes profiles. Human authority remains with Lior and the relevant human owners; profiles coordinate and execute bounded work but do not manufacture authority.

## Topology

```text
Lior ⇄ Discord (single LNX bot identity)
          ├── #pulse ─────────────── Pulse Lead ───── Pulse board
          ├── #algua ─────────────── Algua Lead ───── Algua board
          ├── #meabeclick ────────── MeaBeclick Lead ─ MeaBeclick board
          │                              ├── UX Specialist
          │                              ├── Engineering Specialist
          │                              └── Growth Specialist
          └── other channels/DMs ─── LNX/default
```

LNX is the sole Discord bot identity and portfolio front door. Three explicit channel routes let Lior talk directly with the matching project lead through that same bot; other Discord conversations continue to LNX. This changes the profile handling the channel, not the visible Discord account. Leads own project decomposition and synthesis. Specialists operate only inside their stated duties and return evidence to the MeaBeclick Lead.

## Planes and sources of truth

### Human communication plane

Discord carries requests, alerts, decision prompts, and live voice. It is not the canonical task ledger or evidence store. The three project text channels are explicitly routed to their leads; the bot's existing user allowlist remains unchanged, and internal profiles do not need separate platform accounts.

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

1. Lior sends a request to LNX.
2. LNX decides whether it is a personal commitment, a direct answer, or delegated work.
3. Delegated work is routed to the correct project board and lead.
4. A lead decomposes work and assigns a specialist only when the specialist contract fits.
5. Workers attach verifiable artifacts or references and identify blockers.
6. The lead synthesizes; LNX escalates decisions or reports outcomes to Lior.

## Deliberate non-goals

The system has no external workflow engine, second team-chat plane, bespoke multi-agent runtime, or separate messaging bot for every internal role. Hermes profile routing provides direct lead conversations in the three named project channels; profiles and Kanban supply the required role separation and durable coordination.

## Trust boundary

Profiles isolate Hermes state but are not OS sandboxes. Role contracts constrain behavior; filesystem and credential isolation must be enforced by local deployment controls. Sensitive data must never enter Kanban card bodies, role files, this manifest, or source control.
