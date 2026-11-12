# Autonomous company workflow with Hermes and Paperclip

**Status:** The owner approved the autonomy principle on 2026-11-12: agents own execution end to end; the owner retains direction, exceptions and consequential commitments. The workflow and rollout defaults below are a proposal for review. This document and its planning tasks do not activate agents, grant credentials, approve spending or override project-specific controls.

**Outcome:** Work keeps progressing across Nix projects without the owner repeatedly requesting the next step. Completion has evidence, interrupted work is recoverable, and significant lessons improve subsequent work.

## Starting point and scope

The deployed foundation uses official Hermes and Paperclip images, native Hermes Bot Groups, one Discord bot and native persistent homes. Profile API isolation, model connectivity and private full-state recovery have been exercised. The last inspected Paperclip inventory had one pilot company, three paused agents, no projects and no work items. Complete delegated-work acceptance and scoped external connector tests remain outstanding. Chat connectivity and a successful backup do not establish portfolio readiness.

This design configures operating behavior through the existing products. Paperclip owns agent assignments and execution state; Hermes runs the workers. Project-native research, trading and transactional systems retain their existing responsibilities. No replacement queue, workflow database, general provisioning system or custom agent messaging bridge is proposed.

The canonical human roadmap is the existing Todoist task, [Define the Nix operating harness and consolidate system work](https://app.todoist.com/app/task/6hr424PGJVQMqVQG), in the Nix project. Existing knowledge, Workspace and social-content outcomes remain distinct children; integration work must reuse them rather than create competing roadmaps.

## Authority and decisions

| Scope | Default behavior | Required boundary |
| --- | --- | --- |
| Ordinary execution | Investigate, specify within approved goals, implement, test, draft artifacts, review and maintain work state without per-step permission requests | Authorized workspace/tools, agreed objective, project acceptance criteria and resource limits |
| Policy-governed effects | Perform routine releases, recurring operations, external communications or paid-tool use only within a demonstrated, explicitly approved project policy | Approved scope, permitted effect, account, budget, verification, stop condition and recovery procedure |
| Owner decisions | Bring strategic changes, new consequential commitments, budget/risk exceptions, sensitive access expansion and irreversible actions to the owner | A specific proposal with evidence, alternatives, recommendation and effect of waiting |

Existing grants remain valid within their actual scope. An unknown or absent grant is not permission. An agent can continue safe investigation or drafting while an effect is blocked; it cannot silently broaden scope to finish. Numeric spending/concurrency limits, automatic-release change classes and outbound permissions are intentionally unset until project onboarding records genuine owner decisions. There is no generic spending allowance or permission to contact customers in this design.

An approval binds the actual project, action/scope, version and applicable limits in the project's existing approval mechanism. A broader company principle cannot replace Nixvert's required approvals, Algua's risk/promotion controls or another project's operating rules. Where deterministic execution is already authorized, individual actions need no new approval merely because agents prepared the work. Expired approvals or material scope changes return to the relevant decision owner.

Agents may propose improvements to prompts, skills, tests and procedures. They cannot grant themselves authority, weaken acceptance criteria to pass, raise budgets or promote their own policy changes.

## Sources of truth

| Surface | Owns | Handoff |
| --- | --- | --- |
| Discord | Requests, discussion, concise results and exception notifications | Link the corresponding work item and artifacts; conversation is not the work ledger |
| Todoist | Human-facing outcomes, owner commitments and decisions | One linked commitment per outcome requiring human visibility; no mirror of every internal subtask |
| Paperclip | Internal assignments, dependencies, run/work state, blockers and budgets | One accountable assignee at a time; link source, parent outcome, artifacts and acceptance evidence |
| Hermes | Worker execution, tools, profile continuity and native specialist consultation | Runs receive bounded project context; consultation returns decisions/evidence to the same Paperclip work item |
| GitHub project repositories | Code, product requirements, technical decisions, PRs and CI evidence | Use immutable revisions for completion evidence and preserve existing project authority |
| Drive | Business documents, media and deliverable artifacts | Link final artifacts and versions without copying restricted source data into public repositories |
| Company knowledgebase | Reviewed organizational knowledge and transferable lessons | Retrieve before work; propose significant changes through its actual admission and review process |

Runtime memories, Bot Group discussion and compression summaries are not automatically company truth. Raw transcripts, student records, biometric data, credentials and trading secrets remain in their authorized project stores. Profiles sharing a UID are not access-control boundaries. Tool/account/workspace isolation must match the actual data sensitivity before sensitive work is admitted.

## Project ownership and staffing

Use one Nix portfolio in Paperclip, with projects for real business or research workstreams. A repository is an input, not automatically a separate business, permanent lead agent or team. Shared OS/Kit/library maintenance can share a platform backlog. Each admitted project records:

- Its objective, current milestone, priority and accountable owner.
- Authoritative product/operating documents, repository/workspace and permitted artifact locations.
- Applicable data/access boundaries, resource limits, permitted effects and owner-only decisions.
- Its definition of done, evidence checks, review owner and recovery/data ownership.
- The linked human outcome and dependencies on other projects.

LNX coordinates portfolio priorities, dependencies, capacity and owner decisions. The accountable project lead owns decomposition, delivery, recovery and communication. These are responsibilities, not a requirement for separate always-running management agents. Start with the existing delivery lead, engineer and reviewer for the bounded pilot; add durable project profiles or specialist roles only when recurring work or context separation demonstrates the need.

Specialists receive focused assignments and relevant sources. Research, creative and domain-quality expertise are invoked where the work requires them. A fresh reviewer is separate from the producer for material deliverables, but extra reviewers and group discussions must have a concrete question to resolve. Agreement between agents is not sufficient completion evidence.

Native Hermes profile routes can map project Discord channels/threads to the responsible profile while retaining the same visible bot. Unmatched portfolio requests go to LNX. Direct project conversations retain the work-item identity. Configure and test the actual routes and native Bot Group membership; do not assume task ownership, chat routing and group rosters synchronize automatically.

## Work lifecycle

1. **Capture and identify.** Accept an authorized human request or a bounded project-relevant signal. Find an existing work item before creating one. A chat request and Todoist reference for the same outcome resolve to the same parent. Signals produce proposed work, not automatic authority to change project direction.
2. **Admit and prioritize.** The lead checks the request against project goals, current constraints and capacity. LNX resolves cross-project conflicts. Record dependencies and blocked effects. Prioritization within an approved goal is routine; a strategic change is an owner decision.
3. **Specify and make success testable.** Record the requested outcome, relevant sources, scope, non-goals, constraints and acceptance evidence. Select checks appropriate to the domain before work begins. Routine work can use an established small template; ambiguous work gets a fuller specification. Ask the owner only when a consequential choice or missing authority changes what should be delivered.
4. **Assign and execute.** Paperclip assigns a bounded unit with an accountable owner and appropriate Hermes profile/session/workspace. Use issue-scoped continuity and native run identity. Specialists consult through a native group only when useful, and attach resulting decisions to the task. Scheduling should respond to real work/events and configured routines, rather than consume model calls when no eligible work exists.
5. **Verify independently.** Inspect the actual artifact and behavior against the recorded acceptance criteria. For material deliverables, a fresh reviewer checks the result and its evidence. Failures produce specific corrections and a bounded retry; acceptance cannot be silently rewritten. Unsupported claims remain unresolved even when reviewers agree.
6. **Deliver within authority.** Attach the tested PR, reproducible report or versioned artifact. Publish, merge, deploy, send or spend only where the corresponding effect is permitted. Record approved-but-not-yet-executed, delivered and human-accepted outcomes distinctly using native status/evidence. A blocked external effect does not become a successful delivery.
7. **Close and update human commitments.** The lead summarizes what changed, the evidence, remaining limits and any owner decision. Update the linked Todoist outcome only according to its agreed completion rule. Internal subtasks remain in Paperclip. Duplicate events must not duplicate outcomes or notifications.
8. **Learn and continue.** Record one knowledge-impact disposition: no durable change, update an existing concept, propose a new learning/decision, or escalate conflicting evidence. Reconcile against existing project/KB sources. Pick the next eligible unit within the same approved goal and capacity; finishing a task does not require the owner to request continuation.

The native products need not acquire a new common state machine. Map these lifecycle expectations onto their supported statuses, assignments, comments, approvals and artifacts. If a required guarantee is unsupported, record that gap and keep the affected automation gated until a reviewed solution exists.

## Quality differs by work type

| Work type | Required evidence | Important limit |
| --- | --- | --- |
| Software and operations | Relevant tests, actual user/runtime behavior, independent review and deploy/recovery evidence where applicable | Green unit tests or an HTTP health response alone do not prove the user workflow |
| Teaching and lesson content | Domain rubric, inspected teaching materials, supported math/content and relevant regression examples | Calibrate against owner-approved examples; technical rendering alone does not establish teaching quality |
| Research and ML | Traceable sources/data, reproducible runs, correct splits and defensible interpretation | Respect held-out evaluation and attempt budgets; do not repeatedly evaluate until the result looks favorable |
| Creative work | Approved brief/assets, inspected render, claims/rights/format checks and a calibrated quality rubric | A high self-assigned score is not acceptance; external delivery remains separately governed |
| Commercial work | Supported prospect/customer facts, actual approved artifacts and reconciled provider evidence for effects | Drafts and sandbox receipts are not sent outreach, collected revenue or satisfied customers |

Project-native systems remain authoritative for domain execution and experiment/transaction evidence. The company OS coordinates work and reads the resulting evidence; it does not replace MLflow/Prefect, Algua's deterministic engine or Nixvert's transactional worker and approvals.

## Failure, recovery and escalation

Every work item identifies its next owner and next condition for progress. Native wakeups/routines handle reassignment, completion, dependency release and approved retries where supported. The lead detects stale or failed work, checks current native state, and resumes from durable evidence rather than rerunning everything.

Use explicit attempt, time and cost limits recorded for the work type. Before any uncertain external effect is retried, reconcile its provider/task evidence. Unknown outcomes remain blocked for reconciliation; a retry must not create a duplicate charge, outreach, publication or delivery. Deterministic project controls remain responsible for transactional guarantees.

Escalations contain the blocked outcome, evidence, attempted recovery, recommendation, specific decision needed and consequence of delay. Routine recoverable failures stay with the lead. Exhausted limits, missing authority, unresolved quality disagreements, sensitive access issues or threatened critical commitments reach the appropriate owner. Urgent exceptions can interrupt; ordinary status is batched.

Operational health must include the user path. Exercise an authorized inbound message and response, worker execution, result return and continuation after interruption. A process can be healthy while a connector fails to deliver. Use native monitoring/recovery first; a custom daemon or queue is not the default answer to an unverified gap.

The existing private OS recovery covers its declared Hermes/Paperclip persistent state. Each project's runtime data, research stores and external artifacts need their own explicit recovery ownership and verification. Do not describe the OS recovery image as a backup of every venture.

## The owner's working routine

Recommended defaults, to be calibrated during rollout:

- At a weekly portfolio review, choose outcomes, resolve cross-project priorities and set capacity/budget bounds. Existing product roadmaps and gates remain authoritative.
- During the day, submit requests or feedback in the relevant project conversation; work continues within the approved mandate without approval of each step.
- Receive one concise daily portfolio digest: verified outcomes and artifact links, blocked decisions with recommendations, next milestones, and actual resource use where measurable. The delivery channel/time is selected during setup, not activated by this document.
- Receive urgent exceptions when waiting would materially affect an approved commitment or boundary. Todoist holds actionable owner commitments/decisions; Paperclip offers operational detail when needed.

Measure verified outcomes relative to owner attention, elapsed time and cost. Track intervention reasons, reopen/escaped-defect rates, stale work, repeated failures and evidence quality. Missing cost/attention measurements stay unknown rather than becoming zero. Counts of agents, messages, closed subtasks or code lines do not establish business progress.

## Rollout and acceptance

1. Reconcile the existing source map and roadmap. Retain completed infrastructure work and existing knowledge/Workspace/social tasks. Fix stale pointers through their owning repository's reviewed process; do not import the old VPS configuration or bulk memory.
2. Record the first project's mandate, boundaries and acceptance criteria. Configure the corresponding native Paperclip ownership, workspace, scoped accounts and Hermes routing using existing tools.
3. Prove one bounded engineering outcome with the current three-agent pilot: intake, specification, execution, group consultation where needed, independent verification, artifact return, human-board update and knowledge disposition. Include interruption/recovery and duplicate-event tests.
4. Reuse an existing Workspace or social-content pilot for a bounded non-coding outcome with an appropriate rubric and artifact. Preserve its original scope and permissions; passing the engineering pilot does not prove creative/research quality.
5. Approve narrowly described automatic-effect policies only after their checks, permissions, observability and recovery work in the real project. Until then the affected effect remains blocked or human-operated; safe preparation continues.
6. Expand to the next project/work type based on observed outcomes and actual capacity. Add profiles, roles or automation only for a demonstrated requirement.

Acceptance requires authentic results in the installed Hermes/Paperclip path, not a simulation of their collaboration. Demonstrate correct and rejected access, owner-decision handling, failure/resume, bounded retries, no duplicate effects, quality correction, knowledge handoff and a useful human summary. Future automatic execution depends on this acceptance and real project grants; neither a skill instruction nor an agent-written approval field can supply them.

## Deferred choices and implementation discretion

The owner decides project order, strategic objectives, numeric budgets, consequential permissions and desired notification cadence. These can be captured as onboarding decisions; they do not prevent planning the integration. Existing permissions are inventoried before anyone is asked to reconnect an account.

The implementer chooses supported native status mappings, small naming conventions, technical test seams and routine task decomposition. Document those choices and compatibility gaps. A missing native capability warrants a bounded proposal based on evidence, not automatic replacement infrastructure.

This session's deliverables are the reviewed workflow proposal and deduplicated action items. Live role changes, project activation, new integrations, customer contact, spending and production effects are separate implementation work under the resulting tasks and applicable authority.

## References

- [Current clean deployment architecture](../../CLEAN-COMPANY-OS.md), [deployment guide](../../../deploy/README.md), and [verified recovery](../../DOCKERHUB-RECOVERY.md).
- [Company KB entry and its owned contribution rules](https://github.com/its-nix/knowledgebase/blob/main/START-HERE.md).
- [Paperclip projects](https://docs.paperclip.ing/guides/projects-workflow/projects/) and [native Hermes gateway adapter](https://docs.paperclip.ing/reference/adapters/hermes-gateway/).
- [Hermes multi-profile routing](https://github.com/nousresearch/hermes-agent/blob/main/website/docs/user-guide/multi-profile-gateways.md).
