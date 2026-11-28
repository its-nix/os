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
   `python3 scripts/crm.py qualify --email … --stage SCREENING|MEETING|PROPOSAL|CUSTOMER --rationale "…"` (Twenty's default pipeline; record disqualification as a note)
3. **Draft, don't send.** Any message, call, post or offer to the lead is a founder-gated external action. Write the exact message (at most 3,000 characters) to `$HERMES_HOME/ventures/<id>/drafts/<action-id>.md`, never into a Git checkout, and queue it:
   `python3 scripts/approval.py request --venture <id> --action-id <id>-<lead>-<n> --target "<channel/recipient>" --effect "Send this email to …" --preview <action-id>.md --cost none --expires <date>`
   Use a globally unique action ID and reuse it on retries. Provide an exact preview in plain text. The queue limits the preview to 3,000 characters and rejects backticks and invisible control characters. Expiry is end of day UTC, defaults to seven days from the request, and can be at most 30 days away.
   Add `--revenue-blocking` when waiting on the decision blocks revenue. For a lead reply, include `--lead-received-at` and `--draft-ready-at` when the source times are known. Use ISO 8601 timestamps with a timezone.
   The host delivers the spool request asynchronously. Run `python3 scripts/approval.py status --venture <id> --action-id <action-id>` to refresh the receipt. Until a task ID appears, report `queued-local` as undelivered. Check `queue_reason` for rejected or closed requests. A Todoist completion only sets `queue_status`. A send requires the founder's authenticated decision and a separately authorized bounded method or founder execution. Never retry an ambiguous send.
4. **Outcome.** Record the result only after the founder executed or declined, with evidence:
   `approval.py record --venture <id> --action-id … --outcome executed|declined|expired --evidence "<link>"`
   Then log it on the CRM record with the action id in the title, so a retry never duplicates it: `crm.py note --email … --title "Outcome <action-id>" --body "…"`.
   For an executed send, pass `--founder-sent-at` when the execution evidence records the send time.
5. Link the Kanban card to the CRM record and the receipt path. Don't copy customer details into Kanban comments, shared knowledge or Git.

Contact only people who would reasonably expect it, and follow platform terms and law. Bulk or unsolicited outreach is never a lead's decision.

`crm.py summary --venture <id> [--since <timestamp>] [--until <timestamp>]` reads an enabled venture's Twenty workspace using its lead profile's private `.env`. It returns aggregate stage counts, new leads first seen in the window, and median minutes from opportunity creation to a recorded qualification note in the window. Stage counts use the latest opportunity for each contact and retain standalone opportunities. Qualification timings depend on linked notes written by `qualify` and are unavailable without that evidence. The default window is 24 hours and output contains no keys, contact records, or note text.
