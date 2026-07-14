import argparse
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--run_id", required=True)
parser.add_argument("-i", nargs="+", dest="instance_ids", required=True)
args, _ = parser.parse_known_args()

report = {
    "resolved_ids": args.instance_ids,
    "unresolved_ids": [],
    "error_ids": [],
}
Path(f"{args.run_id}.{args.run_id}.json").write_text(json.dumps(report))
