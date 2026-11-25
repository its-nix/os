"""Deploy-time proof that the pinned Hermes scrubs credentials before ANY memory-provider hand-off.

Hindsight (the memory provider) archives what it is handed and sends it to the extraction model, so
secrets must be removed before the plugin sees the text. The pinned Hermes does that natively: its
MemoryManager passes every provider hand-off (turn sync, recall queries, tool arguments, built-in
memory mirrors, session-end and pre-compress transcripts, delegation results) through
`_redact_for_provider`, which uses the egress scrub (`redact_for_egress`, forced on and fail-closed
regardless of `security.redact_secrets`). The deploy runs this file inside the Hermes image it is
about to start (no network, throwaway container) and refuses the rollout unless it prints exactly
`redaction-probe: ok`.

What this does NOT cover (documented residual risk in deploy/hindsight/README.md): secrets with no
recognisable shape, such as a password written in plain prose.

Samples are assembled from fragments at runtime so this public file holds no credential-shaped
literal. Nothing is printed except category names.
"""

import inspect
import sys

FILLER = 'A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8'
# Every manager method that hands text to a provider must call the scrub.
HAND_OFFS = ('sync_all', 'prefetch_all', 'queue_prefetch_all', 'handle_tool_call', 'on_memory_write',
             'on_turn_start', 'on_session_end', 'on_pre_compress', 'on_delegation')
SCRUBS = ('_redact_for_provider', '_redact_messages_for_egress')


def samples():
    """category -> (text, the secret part that must not survive)."""
    pem_body = FILLER * 2
    return {
        'openai-style key': ('use sk-' + 'proj-' + FILLER * 2, 'proj-' + FILLER * 2),
        'github token': ('token gh' + 'p_' + FILLER + 'wxyz', FILLER + 'wxyz'),
        'aws access key': ('key AK' + 'IA' + 'QWERTYUIOPASDFGH', 'QWERTYUIOPASDFGH'),
        'bearer header': ('Authorization: Bear' + 'er ' + FILLER, FILLER),
        'jwt': ('ey' + 'JhbGciOiJIUzI1NiJ9.' + 'eyJzdWIiOiIxMjM0NTY3ODkwIn0.' + FILLER, FILLER),
        'private key': ('-----BEGIN ' + 'PRIVATE KEY-----\n' + pem_body + '\n-----END ' + 'PRIVATE KEY-----',
                        pem_body),
        'env password': ('DB_PASS' + 'WORD=' + 'Zq9' + FILLER[:12], 'Zq9' + FILLER[:12]),
        'env api key': ('STRIPE_API' + '_KEY=' + 'zz' + FILLER, 'zz' + FILLER),
    }


def check(redact, manager):
    """Names of the failed checks; empty when every sample is masked and every hand-off scrubs."""
    failures = []
    for name, (text, secret) in samples().items():
        try:
            out = redact(text)
        except Exception:
            failures.append('raises: ' + name)
            continue
        if not isinstance(out, str) or secret in out:
            failures.append('leaks: ' + name)
    for method in HAND_OFFS:
        try:
            source = inspect.getsource(getattr(manager, method))
        except Exception:
            failures.append('missing hand-off: ' + method)
            continue
        if not any(scrub in source for scrub in SCRUBS):
            failures.append('unscrubbed hand-off: ' + method)
    return failures


def main():
    sys.path.insert(0, '/opt/hermes')
    try:
        from agent.memory_manager import MemoryManager, _redact_for_provider
    except Exception:
        print('redaction-probe: failed: Hermes memory scrub not importable')
        return
    failures = check(_redact_for_provider, MemoryManager)
    print('redaction-probe: ' + ('failed: ' + '; '.join(failures) if failures else 'ok'))


if __name__ == '__main__':
    main()
