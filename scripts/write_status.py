#!/usr/bin/env python3
"""Write status.json from swebench harness results."""
import json, os, glob, sys

report_dir = os.path.join(os.environ['EVAL_DIR'], 'eval')
agent = os.environ['AGENT_NAME']
preds_path = os.environ['PREDS']
out_json = os.environ.get('OUT_JSON', os.path.join(os.environ['EVAL_DIR'], 'status.json'))

# Read the swebench report
report_path = os.path.join(report_dir, f'{agent}.{agent}.json')
if not os.path.exists(report_path):
    cands = sorted(glob.glob(os.path.join(report_dir, '*.json')), key=os.path.getmtime, reverse=True)
    report_path = cands[0] if cands else None
if not report_path or not os.path.exists(report_path):
    print('WARNING: swebench report not found, skipping status.json')
    sys.exit(0)

with open(report_path) as f:
    rep = json.load(f)

resolved = set(rep.get('resolved_ids', []))
unresolved = set(rep.get('unresolved_ids', []))
errors = set(rep.get('error_ids', []))

# Collect ALL instance IDs from the output directory (not just harness-submitted)
# This includes no_patch, timed_out, container_error instances that never reached the harness
output_dir = os.environ['EVAL_DIR']
all_ids = []
for d in sorted(os.listdir(output_dir)):
    dp = os.path.join(output_dir, d)
    if os.path.isdir(dp) and '__' in d:  # instance dirs contain '__'
        all_ids.append(d)

# Build per-instance details from result.json files
# Harness writes result.json to eval/<iid>/result.json, but also copies to output/<iid>/result.json
instances = {}
for iid in all_ids:
    # Try eval directory first (harness output), then output directory
    rf = os.path.join(report_dir, iid, 'result.json')
    if not os.path.exists(rf):
        rf = os.path.join(os.environ['EVAL_DIR'], iid, 'result.json')
    if not os.path.exists(rf):
        continue
    try:
        meta = json.load(open(rf))
    except Exception:
        meta = {}
    instances[iid] = {
        'status': meta.get('status'),
        'local_eval': meta.get('local_eval'),
        'elapsed_seconds': meta.get('elapsed_seconds'),
        'patch_bytes': meta.get('patch_bytes'),
    }

status = {
    'agent': agent,
    'schema_version': 2,
    'total_instances': len(all_ids),
    'resolved': len(resolved),
    'unresolved': len(unresolved),
    'errors': len(errors),
    'resolved_ids': sorted(resolved),
    'unresolved_ids': sorted(unresolved),
    'error_ids': sorted(errors),
    'instances': instances,
}

with open(out_json, 'w') as f:
    json.dump(status, f, indent=2)
print(f'Wrote status.json: {len(instances)} instances ({len(resolved)} resolved, {len(unresolved)} failed, {len(errors)} errors)')
