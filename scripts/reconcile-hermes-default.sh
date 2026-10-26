#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/reconcile-hermes-default.sh [--apply]

Preview (default) inspects the Hermes update plan and prints the non-secret
configuration reconciliation. --apply creates Hermes' built-in pre-update
backup outside this repository, updates a safe upstream checkout, reconciles
the default profile, and runs health/status commands.

Apply fails closed when Hermes reports carried local commits or an unrecognized
carried-state marker. Review and merge that fork manually before retrying; this
script never replaces local history.
EOF
}

apply=false
case "${1:-}" in
  "") ;;
  --apply) apply=true ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }

HERMES_BIN="${HERMES_BIN:-hermes}"
command -v "$HERMES_BIN" >/dev/null 2>&1 || {
  printf 'error: Hermes executable not found: %s\n' "$HERMES_BIN" >&2
  exit 1
}

version_output="$("$HERMES_BIN" --version)"
printf '%s\n' "$version_output"

carried_commits=0
carried_state=none
version_output_lower="${version_output,,}"
if [[ "$version_output" =~ \(\+([0-9]+)[[:space:]]carried[[:space:]](commit|commits)\) ]]; then
  carried_commits="${BASH_REMATCH[1]}"
  carried_noun="${BASH_REMATCH[2]}"
  canonical_marker="(+$carried_commits carried $carried_noun)"
  version_without_marker="${version_output/"$canonical_marker"/}"
  if { [[ "$carried_commits" == 1 ]] && [[ "$carried_noun" == commit ]]; } ||
     { [[ "$carried_commits" =~ ^(0|[1-9][0-9]*)$ ]] && [[ "$carried_commits" != 1 ]] && [[ "$carried_noun" == commits ]]; } &&
     [[ "${version_without_marker,,}" != *carried* ]]; then
    carried_state=canonical
  else
    carried_state=unrecognized
  fi
elif [[ "$version_output_lower" == *carried* ]]; then
  carried_state=unrecognized
fi

cat <<'EOF'

Default-profile reconciliation:
  hermes config migrate
  hermes config set approvals.mode manual
  hermes config set approvals.cron_mode deny
  hermes config set delegation.max_concurrent_children 3
  hermes config set delegation.max_iterations 50
  hermes config set display.tool_progress all
  hermes config set display.background_process_notifications all
  hermes config unset display.tool_progress_overrides
  hermes config unset delegation.max_async_children
EOF

if [[ "$carried_state" == unrecognized ]]; then
  printf '\nBLOCKED: Hermes version output contains an unrecognized carried-state marker; refusing to treat the checkout as clean.\n' >&2
  printf 'Review the Hermes source checkout and version output before retrying.\n' >&2
  if [[ "$apply" == true ]]; then
    exit 4
  fi
  exit 0
fi

if [[ "$carried_commits" != 0 ]]; then
  printf '\nBLOCKED: Hermes reports %s carried local commit(s); refusing an in-place update.\n' "$carried_commits" >&2
  printf 'Review the Hermes fork and its upstream divergence before running --apply.\n' >&2
  if [[ "$apply" == true ]]; then
    exit 3
  fi
  exit 0
fi

printf '\nHermes update plan (read-only):\n'
"$HERMES_BIN" -p default update --plan

if [[ "$apply" != true ]]; then
  printf '\nPREVIEW only; rerun with --apply after reviewing the update plan.\n'
  exit 0
fi

on_apply_error() {
  local status=$?
  trap - ERR
  printf '\nERROR: Hermes update/config reconciliation failed; the update or configuration may be partially applied.\n' >&2
  printf 'Correct the reported error and rerun this command. If rollback is needed and Hermes printed a backup path, stop the gateway before restoring:\n' >&2
  printf '  hermes gateway stop\n' >&2
  printf '  hermes import <backup.zip>\n' >&2
  exit "$status"
}
trap on_apply_error ERR

unset_config_key_if_present() {
  local key="$1"
  local output
  local status

  if output="$("$HERMES_BIN" -p default config get "$key" 2>&1)"; then
    "$HERMES_BIN" -p default config unset "$key"
    return
  else
    status=$?
  fi

  if [[ "$status" -eq 1 && "$output" == "Config key not set: $key" ]]; then
    printf 'Config key already absent: %s\n' "$key"
    return
  fi

  [[ -z "$output" ]] || printf '%s\n' "$output" >&2
  return "$status"
}

verify_config_value() {
  local key="$1"
  local expected_json="$2"
  local actual_json

  if ! actual_json="$("$HERMES_BIN" -p default config get "$key" --json)"; then
    printf 'config readback failed or key is missing: %s\n' "$key" >&2
    return 1
  fi
  if ! python3 - "$key" "$expected_json" "$actual_json" <<'PY'
import json
import sys

key, expected_text, actual_text = sys.argv[1:]
try:
    expected = json.loads(expected_text)
    actual = json.loads(actual_text)
except json.JSONDecodeError as error:
    raise SystemExit(f"config readback for {key} was not valid JSON: {error}")
if type(actual) is not type(expected) or actual != expected:
    raise SystemExit(
        f"config readback mismatch for {key}: expected {expected_text}, got {actual_text}"
    )
PY
  then
    return 1
  fi
}

# --backup asks Hermes to create its own pre-update snapshots outside this repo.
"$HERMES_BIN" -p default update --backup --yes
post_update_version="$("$HERMES_BIN" --version)"
printf '\nPost-update Hermes version:\n%s\n' "$post_update_version"
"$HERMES_BIN" -p default config migrate
"$HERMES_BIN" -p default config set approvals.mode manual
"$HERMES_BIN" -p default config set approvals.cron_mode deny
"$HERMES_BIN" -p default config set delegation.max_concurrent_children 3
"$HERMES_BIN" -p default config set delegation.max_iterations 50
"$HERMES_BIN" -p default config set display.tool_progress all
"$HERMES_BIN" -p default config set display.background_process_notifications all
verify_config_value approvals.mode '"manual"'
verify_config_value approvals.cron_mode '"deny"'
verify_config_value delegation.max_concurrent_children '3'
verify_config_value delegation.max_iterations '50'
verify_config_value display.tool_progress '"all"'
verify_config_value display.background_process_notifications '"all"'
unset_config_key_if_present display.tool_progress_overrides
unset_config_key_if_present delegation.max_async_children
"$HERMES_BIN" -p default config check
"$HERMES_BIN" -p default doctor
"$HERMES_BIN" -p default gateway status

trap - ERR
printf '\nReconciliation commands completed successfully. Review config-check, doctor, and gateway-status output above for warnings or degraded state.\n'
