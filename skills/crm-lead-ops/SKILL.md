---
name: crm-lead-ops
description: "Use when a venture lead handles inbound or researched leads: intake into Twenty CRM, qualification, outcome logging, and any outreach that needs founder approval."
license: MIT
---

# Lead intake → qualification → approved action → outcome

The venture's Twenty workspace is the source of truth for contacts, pipeline and activity. Run these deterministic steps with the bundled scripts; use judgment only for qualification and for drafting.

1. **Intake** (idempotent by email; creates the person, an opportunity at `NEW`, and an intake note):
   `python3 scripts/crm.py intake --email … --first … --last … --company … --source "<where it came from>"`
2. **Qualify** against the charter's ICP. Write a short rationale that cites evidence, not vibes:
   `python3 scripts/crm.py qualify --email … --stage SCREENING|MEETING|PROPOSAL|CUSTOMER|LOST --rationale "…"`
3. **Draft, don't send.** Any message, call, post or offer to the lead is a founder-gated external action. Write the exact message to a file and queue it:
   `python3 scripts/approval.py request --venture <id> --action-id <id>-<lead>-<n> --target "<channel/recipient>" --effect "Send this email to …" --preview draft.md --cost none --expires <date>`
   A request is idempotent by action id. Without a queue credential it stays `queued-local` and is reported as a blocker. Never send it yourself, and never retry an ambiguous send.
4. **Outcome.** Record the result only after the founder executed or declined, with evidence:
   `approval.py record --venture <id> --action-id … --outcome executed|declined|expired --evidence "<link>"`
   Then log it on the CRM record: `crm.py note --email … --title "Outreach outcome" --body "…"`.
5. Link the Kanban card to the CRM record and the receipt path. Don't copy customer details into Kanban comments, shared knowledge or Git.

Contact only people who would reasonably expect it, and follow platform terms and law. Bulk or unsolicited outreach is never a lead's decision.
