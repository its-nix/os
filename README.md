# Nix OS

Nix OS is the company operating setup built on official Hermes and Paperclip containers. It uses a fresh three-profile configuration, one shared model login, Discord's LNX bot, native specialist discussion and the company knowledgebase. It is an organizational operating system, not a Linux distribution.

## Current setup

| Component | Running configuration |
| --- | --- |
| Hermes | Official pinned image, container `nixos-hermes`, native gateway and multiplexed profiles |
| Paperclip | Official pinned image, container `nixos-paperclip`, internal assignments and execution |
| Profiles | Delivery Lead (`default`), Engineer (`engineer`), Independent Reviewer (`reviewer`) |
| Model | `openai-codex` / `gpt-6.1-terra`; one login in the main profile, native credential fallback for specialists |
| Discord | Existing LNX bot connected to the main profile; specialists have Discord disabled |
| Specialist discussion | Native Bot Group `nix-delivery-pilot` using those same profiles |
| Company knowledge | Read-only pinned snapshot of [its-nix/knowledgebase](https://github.com/its-nix/knowledgebase) |
| Delivery | Verified main commits deploy through restricted SSH as `nixos`; daily/manual reconciliation |

Todoist owns human–agent commitments, GitHub owns code and project technical evidence, Drive owns business artifacts, and the company KB owns reviewed organizational knowledge. Paperclip owns internal work. The [architecture](docs/CLEAN-COMPANY-OS.md) defines the handoff between these stores.

## Verification and remaining acceptance

As of 2026-11-09, both services are healthy, all three profiles have returned live model responses through the shared login, and the native gateway reports Discord and API connected. Profile key isolation, selected skill loading, the native Group roster and KB retrieval/tests have been verified. Paperclip's three agents remain paused with heartbeat and wake-on-demand disabled.

The full Paperclip-led specification → implementation → independent-review task, group consultation, real Todoist/GitHub/Drive connector permissions and knowledge-impact handoff remain pending. The full-system Docker Hub recovery implementation is documented in [Daily private OS recovery](docs/DOCKERHUB-RECOVERY.md). The real VPS local cold snapshot and isolated Hermes/Paperclip restore passed on 2026-11-10. Private repository creation, publication and scheduling await the native Docker Hub login and a successful remote restore check. A connected bot or successful model probe does not complete those checks.

## Configure and operate

- [Deployment and inputs](deploy/README.md): official image/source pins, fresh-home preparation and native configuration.
- [VPS access and CI/CD](docs/OS-DELIVERY.md): SSH, dashboards, shared provider login, Discord setup and deployment receipts.
- [Daily private recovery](docs/DOCKERHUB-RECOVERY.md): full native state, private Docker Hub publication, verification and restoration.

`deploy/compose.yaml` declares the two services, `deploy/versions.json` pins their dependencies, and `deploy/pilot.json` declares profiles, selected skills, model defaults and the Discord-owning profile. Fresh preparation consumes those settings. Existing native state is updated through native tools and explicit input verification; preparation never overwrites it.

Credentials and Discord allowlist/home-channel values stay in native private stores. Memory, sessions, profile auth and Paperclip databases live in persistent VPS homes and do not belong in Git.

## Configuration policy

Start from the fresh configuration and add roles or automation when real tasks justify them. Carry useful company/project knowledge forward through reviewed KB contributions and focused role instructions. The old runtime configuration, context files, memories and sessions were not bulk imported; the reusable old company/project context has not yet received a complete audit.

The previous seven-profile manifest, role packs, monitoring and bootstrap remain [historical reference](docs/LEGACY-OS.md). They are preserved for audit and are not inputs to the running stack.
