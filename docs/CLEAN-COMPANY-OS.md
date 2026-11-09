# Nix OS architecture

This is the current architecture and its acceptance contract. Start fresh, preserve reviewed company knowledge, and expand configuration through observed needs in real work. The older manifests, installers and receipts describe the previous deployment; they are not inputs to the clean stack.

## Purpose and ownership

Use official Hermes and Paperclip images, native Bot Groups and existing company knowledge tooling. Start with a three-agent pilot: a delivery lead, an engineer and an independent reviewer. Expand to the portfolio roles only after the pilot passes. Agency Agents supplies bounded role expertise; NixKit supplies specification and verification procedures. Role prompts constrain behavior but are not security boundaries.

| Component | Owns |
| --- | --- |
| Todoist | Human–agent commitments, requested outcomes and human decisions |
| Paperclip | Internal agent assignments, execution state, scheduling and budgets |
| Hermes | Specialist execution, tools, memories and sessions |
| Hermes native Bot Groups | Shared specialist discussion and consultation |
| Company KB | Reviewed organizational facts, decisions, methods and transferable lessons |
| GitHub project repositories | Code, technical project truth, PRs and CI evidence |
| Drive | Business documents, media and deliverable artifacts |

Paperclip replaces the previous delegated-work queue. A Todoist commitment links to its internal Paperclip work; internal subtasks are not mirrored into the human board. Work originating in a group must retain the same work-item identity. Conversations are context and evidence, not a competing task ledger.

## Deployment

Fresh Compose project `nixos`, fresh state directories and new service credentials. Do not import, mount, restart or patch the previous VPS deployment or its data. Leave unrelated VPS workloads unchanged. Physical removal of old data is outside this rollout.

Use the official image entrypoints and supervision unchanged. Pin images by digest and record source revisions. One Hermes gateway multiplexes the pilot profiles; Paperclip uses its built-in `hermes_gateway` adapter with `/p/<profile>` base URLs and distinct profile API keys. Bot Groups use those same profiles. Confirm the endpoints and profile-key scoping against the pinned runtime before relying on the mapping.

For this single Linux VPS, host networking and loopback-only listeners keep Paperclip-to-Hermes URLs local without a proxy or an insecure-remote-HTTP override. Allocate fresh ports: Hermes API 18642, native UI 19119 where supported, Paperclip 13100. Operator access is through SSH forwarding. No Docker socket, broad host-home mounts or inherited administrator credentials are given to either service. This is a trusted single-operator, non-sensitive pilot; profile separation is not an OS sandbox.

Paperclip uses its supported local-trusted mode on loopback. Persistent state remains in the native homes. OpenAI Codex authentication is configured once in the main Hermes profile; both specialists use the native global-root credential fallback. The selected provider/model is declared in `deploy/pilot.json`. All three profiles have returned live model responses. Do not copy OAuth token files between profiles. Keep unattended execution disabled until the full workflow is accepted.

The existing Discord bot LNX connects to the main profile. Specialist profiles have Discord explicitly disabled and consult through the native Bot Group. Require mentions and retain the authorized-user allowlist in the native private configuration. Bot credentials, user IDs and home-channel IDs are not committed to Git.

## Knowledge loop

Canonical entry: <https://github.com/its-nix/knowledgebase/blob/main/START-HERE.md>. The KB owns note schema, admission, validity, freshness and review. Reuse its existing retrieval, validation and curation scripts; do not add a provider, global index or ingestion daemon.

Before work, retrieve relevant current knowledge and retain canonical citations. At significant outcomes, record one knowledge-impact disposition: no durable change, update an existing concept, propose a new learning/decision, or escalate conflicting evidence. Search before capture. The owning lead reviews project evidence; the portfolio curator routes cross-company proposals; applicable human authority approves consequential promotion. Raw chats, compaction summaries, private runtime memory and protected records are not ingested.

KB notes link to Drive artifacts and project-owned truth without copying them. Provisional/disputed/superseded/overdue notes keep their status and are excluded from current-truth answers. The pilot receives a read-only, immutable KB checkout with no repository credential. Proposal writes later use a separate scoped workspace and reviewed PR; no worker receives merge rights. The [KB workflow contract update](https://github.com/its-nix/knowledgebase/pull/5) is merged. The runtime still uses the immutable snapshot pinned in `deploy/versions.json`; a repository merge does not silently refresh that snapshot.

## Verification and rollout

Prepare the official containers and independent persistent homes; verify versions, native API capabilities, profile-scoped key rejection and Bot Group storage/driver readiness. Prove the private KB's own test, retrieval and curation commands in the runtime. Declare the same three identities in Paperclip and a native group, then verify readback rather than assuming roster synchronization.

Model connectivity is verified. Next, run one bounded specification → implementation → independent verification task, including a group consultation and a knowledge-impact result. Verify GitHub/Drive links and the linked human-facing Todoist outcome with scoped connectors. These external writes require real configured accounts; fixture success is not live connector proof.

Recovery must cover native Hermes full state (including groups), Paperclip's database/configuration/keys and a manifest of image/KB/skill revisions. Preserve dated private images without extra encryption. Adapt the separate recovery PR to container-native capture and the actual Paperclip database backend; test an isolated restore before enabling daily publication.

The pilot is not complete until the full delegated workflow, knowledge review, actual connector permissions and restore checks pass. Successful model responses and a connected Discord bot establish connectivity, not full acceptance. Unsupported native integration is a compatibility blocker, not permission to add a custom bridge or patch upstream internals.

## Carrying context forward

Retain the fresh runtime configuration. The active SOULs contain new Nix role boundaries, Agency role expertise and company-KB access instructions; the selected Kit skills supply execution procedures. Older repository roles/configuration remain historical and are not loaded by the workers. Old runtime memory and sessions were not imported. Review reusable company/project facts and proven preferences selectively before adding them to the KB or role instructions. A complete audit of that old context remains pending.
