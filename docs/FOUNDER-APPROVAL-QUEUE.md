# Founder approval queue

Hermes writes requests to `/run/nix-approvals-outbox` and reads status by a random per-request filename from `/run/nix-approvals-status`. The status directory is mounted read-only. Neither directory is listable by the Hermes user. Venture receipts and spool lookups provide logical scoping. Hermes profiles share one Unix user, so a profile that can read another profile's receipt can also read that request's status. Other Compose services receive neither mount.

A host timer runs the reviewed worker using the host's `/usr/bin/python3` in isolated mode and the standard library. There is no listener. The worker checks the configured private `Nix approvals` project, reads tasks only in that project, and sends only queue creation and expiry closure commands. Its SQLite mapping enforces a global action ID and preserves the admitted payload across retries. It reconciles active tasks and up to 89 days of completed tasks by action ID before creating. Todoist deduplicates retried creates with a stable Sync command UUID. A mapped task that disappears is closed locally and is never recreated. [Todoist API v1](https://developer.todoist.com/api/v1/)

The timer waits 90 seconds between runs, with up to 20 admissions and five checks or mutations per run. While the deploy/recovery lock is held, it skips that run. An unavailable Todoist request stays queued for later reconciliation. `pending` can mean the host accepted the request but Todoist delivery is still unconfirmed. `completed-by-founder` means the matching task is in completed history. `expired` means the deadline passed and the worker either closed the matching task or found no task to close. `closed` marks a rejected request, a conflict, or a task that is no longer safely manageable. None of these statuses changes the action outcome. The lead records an outcome independently, with evidence, and links the receipt from the origin Kanban card.

## First activation on the VPS

1. Wait for the merged revision's healthy deploy receipt. In the founder's own Todoist account, use an active, unshared personal project named `Nix approvals` and create the `nix-approval` label if needed. Use the v1 project ID from the Todoist project URL. Keep the project's queue tasks in place until reconciliation. Use a fresh action ID for any new proposed action.
2. Through an operator-controlled secret transfer, place the founder's existing Todoist API token at `/srv/nix-clean/todoist/token`. The directory must be a real `root:root` directory with mode `0700`. The file must be a regular `root:root` file with mode `0600`, holding only the raw API token in ASCII and an optional final LF. At `/srv/nix-clean/todoist/project-id`, use the same ownership and file mode with only the project ID and an optional final LF. Keep the token out of shell arguments, command output, Hermes profile environments, and Docker. If a lead received a Todoist API token under the old instructions, revoke that token and clear it from the profile before activating this queue.
3. Verify the files and project access without printing credentials, then install and enable the reviewed units as root.

   ```sh
   sudo /usr/bin/python3 -IB /opt/nix-clean-source/scripts/approval/worker.py --check
   sudo install -o root -g root -m 0644 \
     /opt/nix-clean-source/scripts/approval/nixos-approval.service \
     /opt/nix-clean-source/scripts/approval/nixos-approval.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now nixos-approval.timer
   sudo systemctl is-active nixos-approval.timer
   ```

The check reads just the configured project and prints a fixed success message. Each deploy refreshes already installed units. For recovery, the cold snapshot captures the outbox, status directory, mapping and project ID. It excludes the account-wide token. Restore the state first, provision the token separately, then run the check and install the units on the replacement host. Activate the queue on only one host. For rollback that removes the worker, disable `nixos-approval.timer` first. Reconcile old `submitting` receipts against Todoist manually before creating any replacement request. They may refer to a task created by the old direct integration, including outside `Nix`.

## Acceptance H after deploy

Make a disposable Kanban card for an existing test venture. Use a never-before-used action ID and a harmless privileged effect, such as creating a root-only marker under the unmounted Todoist configuration directory. The founder should use the Todoist UI to complete the queue item and should leave the proposed effect unexecuted throughout the demo.

1. On the VPS, confirm the prospective marker is absent. Use its name as the action ID, or replace the suffix consistently for a repeat run.

   ```sh
   sudo test ! -e /srv/nix-clean/todoist/acceptance-h-demo-001
   ```

2. On the Kanban card ask the test venture lead to queue this request from its installed `crm-lead-ops` skill. Run the following as the lead in Hermes. Replace `demo` if using another test venture, and use the skill's installed directory as the working directory.

   ```sh
   venture=demo
   action=acceptance-h-demo-001
   drafts="/opt/data/ventures/$venture/drafts"
   mkdir -p "$drafts"
   printf '%s\n' 'install -m 0600 /dev/null /srv/nix-clean/todoist/acceptance-h-demo-001' > "$drafts/$action.md"
   python3 scripts/approval.py request --venture "$venture" --action-id "$action" \
     --target 'VPS root-only test marker' --effect 'Create the acceptance H test marker' \
     --preview "$action.md" --cost none --executor founder
   ```

3. After a timer run, have the lead check `python3 scripts/approval.py status --venture "$venture" --action-id "$action"`. Expect `queue_status: pending`, a task ID and `outcome: null`. In Todoist, confirm one task in `Nix approvals` labeled `nix-approval` with title `[approval] demo: Create the acceptance H test marker`. Compare the description, including the exact command preview, against the request. Repeat the same request and confirm it returns the same task ID. Run the root marker absence check again.
4. Complete the Todoist task using its checkbox. After another timer run, check the lead's receipt again. Expect the same task ID and `queue_status: completed-by-founder` with `outcome: null`. Run the root marker absence check a third time. Record these three observations and the task ID on the originating Kanban card. The checkbox alone provides no evidence for `record --outcome executed`.

The deployment and this live demo are operator steps. Unit tests use simulated Todoist and queue files.
