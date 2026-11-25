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

from unittest.mock import Mock
import sys

FILLER = 'A1b2C3d4E5f6G7h8J9k0L1m2N3p4Q5r6S7t8'


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


def check(manager):
    """Check the arguments actually delivered, including queued work and nested transcripts."""
    provider = Mock()
    provider.name = 'hindsight'
    provider.get_tool_schemas.return_value = [{'name': 'probe_memory', 'parameters': {'type': 'object'}}]
    provider.prefetch.return_value = provider.on_pre_compress.return_value = ''
    provider.handle_tool_call.return_value = '{}'
    manager.add_provider(provider)
    failures = []
    for category, (text, secret) in samples().items():
        messages = [{'role': 'user', 'content': text}]
        provider.reset_mock()
        manager.sync_all(text, text, messages=messages)
        manager.prefetch_all(text)
        manager.queue_prefetch_all(text)
        manager.handle_tool_call('probe_memory', {'nested': [text]})
        manager.on_memory_write('add', 'memory', text, metadata={'previous_content': text})
        manager.on_turn_start(1, text)
        manager.on_session_end(messages)
        provider.pre_compress_checkpoint_api_version = 1
        manager.on_pre_compress(messages)
        provider.pre_compress_checkpoint_api_version = 2
        manager.on_pre_compress([], evidence_messages=messages, require_checkpoint=True)
        manager.on_delegation(text, text)
        if not manager.flush_pending(timeout=10):
            return ['timed out waiting for provider']
        for hook in ('sync_turn', 'prefetch', 'queue_prefetch', 'handle_tool_call', 'on_memory_write',
                     'on_turn_start', 'on_session_end', 'on_pre_compress', 'on_delegation'):
            calls = getattr(provider, hook).call_args_list
            if len(calls) != (2 if hook == 'on_pre_compress' else 1):
                failures.append('missing delivery: ' + hook)
            elif any(secret in repr(call) for call in calls):
                failures.append('leaks: ' + hook + ' / ' + category)
    return failures


def main():
    sys.path.insert(0, '/opt/hermes')
    try:
        from agent.memory_manager import MemoryManager
    except Exception:
        print('redaction-probe: failed: Hermes memory scrub not importable')
        return
    failures = check(MemoryManager())
    print('redaction-probe: ' + ('failed: ' + '; '.join(failures) if failures else 'ok'))


if __name__ == '__main__':
    main()
