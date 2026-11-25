"""Read the effective memory provider in the candidate image, without initializing a gateway."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, '/opt/hermes')
from dotenv import dotenv_values
from hermes_cli import config

home = Path(os.environ['HERMES_HOME'])
# Refuse ambiguous config instead of guessing how a gateway's environment would expand it.
if (home / 'config.yaml').exists():
    raw = config.read_config_doc(home / 'config.yaml') or {}
    if '$' in str((raw.get('memory') or {}).get('provider', '')):
        raise ValueError('Dynamic memory provider')
# load_config normally creates the home skeleton. The real home is mounted read-only.
config._HERMES_HOME_ENSURED.add(str(home))
provider = config.load_config().get('memory', {}).get('provider')
if provider == 'hindsight':
    # The plugin falls back to other config sources if Hermes cannot read this file.
    with (home / 'hindsight/config.json').open() as handle:
        json.load(handle)
overrides = any(key.startswith('HINDSIGHT_') for key in dotenv_values(home / '.env'))
print(json.dumps([provider, overrides]))
