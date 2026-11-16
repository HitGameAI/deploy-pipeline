#!/usr/bin/python3
"""Deny locked upstream Git namespaces without logging remote URLs or credentials."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

real = os.environ.get('MODULE_CUSTODY_REAL_GIT', '')
if not Path(real).is_absolute(): sys.exit(126)
owners = json.loads(os.environ.get('MODULE_CUSTODY_BLOCKED_OWNERS', '[]'))
pattern = re.compile(r'github\.com(?::|/)+(?:' + '|'.join(re.escape(x) for x in owners) + r')(?:/|$)', re.I)
probe = subprocess.run([real, '-c', 'core.fsmonitor=false', 'config', '--get-regexp', r'^remote\..*\.url$'], capture_output=True, text=True)
if owners and any(pattern.search(x) for x in sys.argv[1:] + [probe.stdout]):
    print('Locked upstream Git namespace denied', file=sys.stderr)
    sys.exit(126)
os.execve(real, [real] + sys.argv[1:], os.environ)
