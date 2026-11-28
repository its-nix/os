---
name: google-drive
description: Work with approved Nix notes and deliverables in the founder’s Google Drive using gws.
license: MIT
---

# Nix in Google Drive

- Use `gws drive` inside Hermes. Use `gws schema drive.files.list` or the relevant method schema for flag and field names. If authentication fails, ask the founder to restore the grant.
- Resolve **Nix** on each task. List files with `'root' in parents and name = 'Nix' and mimeType = 'application/vnd.google-apps.folder' and trashed = false`. Request `nextPageToken,files(id,ownedByMe)`, follow all pages, and require exactly one owned match. Keep IDs in local command data. Never hardcode or print folder IDs in logs, reports, or PRs. If the match is missing or ambiguous, ask the founder.
- Work Markdown-first under **Nix**. Check the target’s parent chain before any write or move. Work on contents within that tree and keep ordinary Markdown files as Markdown. For native Google Docs, Sheets, and Slides, keep a sibling `<title>.index.md` note with YAML frontmatter, a one-line summary, and its `webViewLink`, using the audience’s existing access. Treat shortcuts as links.
- Use the deterministic helper `/opt/data/skills/nix-os/google-drive/scripts/vault.py` for Obsidian-compatible vault maintenance (resolves the single owned **Nix** folder, never overwrites existing files, never prints raw Drive IDs):
  - `vault.py bootstrap [--dry-run]`: idempotently ensure `Nix/START-HERE.md`, `Nix/CONVENTIONS.md`, `Nix/.obsidian/app.json`, `Nix/Company/`, and `Nix/Ventures/`; report differences on existing files without overwriting.
  - `vault.py ensure-venture <id> [--name NAME] [--mission TEXT] [--receipt PATH] [--dry-run]`: idempotently ensure `Nix/Ventures/<id>/` and its `README.md` index note; persist and reuse a local redacted receipt (SHA-256 fingerprints only).
  - `vault.py index-native <folder> [--no-recursive] [--dry-run]`: write `<title>.index.md` for each native Google Doc, Sheet, or Slides in `<folder>` lacking an index note.
  - `vault.py lint [--folder PATH]`: verify required YAML frontmatter keys (`title`, `type`, `venture`, `status`, `updated`, `source`) and reject absolute Drive URLs for local notes.
- Never trash or delete anything outside **Nix**. Never share, invite, transfer ownership or change permissions, including with a link. The founder handles those actions under `docs/AUTHORITY.md`. Never bulk-ingest customer data.
- Use the reviewed policy snapshot at `/knowledge/company`. Propose policy changes by evidence-linked PR against the Git KB.
- Each Hermes profile can use the same account-wide Drive grant. Keep credentials, client JSON and raw Drive output out of T3, the reviewer, and public artifacts.
