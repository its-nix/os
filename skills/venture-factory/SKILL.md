---
name: venture-factory
description: "Use when the founder brings a new business idea, or asks to create, pause, resume, retire or inspect a venture."
license: MIT
---

# Venture Factory

Turns an idea into an approved, provisioned venture with its own accountable Hermes lead. Generic: no venture is built in, and the registry holds 0..N ventures.

## 1. Discovery (adaptive grilling)
Use the core discovery skills (`brainstorming`, grilling/spec skills when installed). Ask one question at a time, only about things the founder can decide or knows: the customer, the problem, the offer, what they will and will not do, constraints, money and time limits. Research market facts yourself rather than asking. Stop when the critical contradictions are resolved and the remaining unknowns are things only the market can answer; list those as hypotheses.

## 2. Decision package
Write two Markdown files in a scratch folder:
- **PRD.md**: ICP/customer, problem and offer, MVP or service scope, constraints, measurable outcomes, risks, explicit unknowns, and the **first mission**: the single most important commercial or operational uncertainty (interviews, offer test, feasibility, service pilot or implementation; no universal paid-probe gate).
- **GTM.md**: acquisition hypothesis and channel, first conversion path, unit-economics hypotheses, metrics, and which external actions will need founder approval.

Decide the packs from `core, engineering, product, growth, sales, content, design, research, operations, data-ai`. Enable CRM only if the venture handles leads or customers now.

## 3. Founder strategic approval (once)
Send the founder a short decision summary: name, id, mission, packs, CRM yes/no, and links to the two files. Ask for **approve / revise / defer**. Revise loops back to discovery; defer archives the package with a note. Approval is the founder's explicit reply. Record its message link or a decision note as the approval reference. Never treat silence as approval.

## 4. Provision (idempotent)
Run inside the Hermes runtime:

```bash
python3 /opt/data/skills/nix-os/venture-factory/scripts/venture.py create --dry-run \
  --id <id> --name "<Name>" --mission "<first mission>" --approval-ref "<link or note>" \
  --prd PRD.md --gtm GTM.md --pack product --pack growth [--crm] [--repo owner/name]
```

`--prd` and `--gtm` are required: a lead judges every loop against them, so a venture without them cannot measure progress. Review the plan, then repeat without `--dry-run`. Re-running is safe. The tool:
- creates the registry entry (`$HERMES_HOME/ventures/<id>/`) with the manifest, CHARTER, PRD and GTM
- creates the lead profile `<id>` and its SOUL
- creates the named Kanban board `<id>`
- creates the daily outcome and weekly distillation loops as native cron jobs, plus the first mission card assigned to the lead

It refuses to adopt any pre-existing profile it did not create, so historical or legacy profiles are never reactivated. The new profile is served after the gateway reloads; the LNX/ops runbook covers this.

The OS deployment installs `venture-lead` into the new lead's `skills/nix-os/`. Restart the gateway or run the next deploy so it serves the new profile. Request a CRM workspace or repository only if the manifest says it is needed.

## 5. Lifecycle
`venture.py pause|resume|retire --id <id> --reason "<why>"` and `venture.py stage --id <id> --to operating|scaling|pivoting`. Pause and retire stop the lead's routines; nothing is deleted. `venture.py status` lists the registry.

## Known limits
Provisioning is crash-safe and serialized per venture. Two things are out of scope: an operator hand-creating the same profile or board slug *during* a provisioning retry, and archiving the mission card before an interrupted first run recorded it. In either case, check `venture.py status` and fix the state deliberately.
