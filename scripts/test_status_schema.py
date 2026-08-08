#!/usr/bin/env python3
"""Validate status.json schema for T4-26 test."""
import json, sys, os

status_path = os.path.join(os.environ.get('REPO_ROOT', '.'), 'workspace', 'outputs', 'pi', 'status.json')
required = ['agent', 'schema_version', 'total_instances', 'resolved', 'unresolved', 'errors', 'instances']

try:
    with open(status_path) as f:
        data = json.load(f)
    missing = [k for k in required if k not in data]
    if missing:
        print('MISSING:' + ','.join(missing))
        sys.exit(1)
    else:
        print('OK:' + str(data['total_instances']) + ':instances')
except Exception as e:
    print('ERROR:' + str(e))
    sys.exit(1)
