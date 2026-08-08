#!/usr/bin/env python3
"""Validate status.json includes agent-failed instances for T4-27 test."""
import json, sys, os

status_path = os.path.join(os.environ.get('REPO_ROOT', '.'), 'workspace', 'outputs', 'pi', 'status.json')

try:
    with open(status_path) as f:
        data = json.load(f)
    counts = {}
    for iid, inst in data['instances'].items():
        s = inst.get('status', 'unknown')
        counts[s] = counts.get(s, 0) + 1
    has_no_patch = counts.get('no_patch', 0) > 0
    has_timed_out = counts.get('timed_out', 0) > 0
    has_container_error = counts.get('container_error', 0) > 0
    if has_no_patch and has_timed_out and has_container_error:
        print('OK:' + str(counts))
    else:
        print('MISSING:' + str(counts))
        sys.exit(1)
except Exception as e:
    print('ERROR:' + str(e))
    sys.exit(1)
