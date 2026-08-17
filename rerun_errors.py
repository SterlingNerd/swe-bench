#!/usr/bin/env python3
"""Re-run errored and container errored instances using the Runner API."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from swebench_orchestrator.config import Config
from swebench_orchestrator.runner import Runner

def main():
    config = Config.from_env()
    runner = Runner(config)
    
    # Errored instances (3)
    errored = [
        "astropy__astropy-8707",
        "astropy__astropy-8872",
        "django__django-11066",
    ]
    
    # Container error instances (7)
    container_errored = [
        "django__django-12406",
        "django__django-13449",
        "django__django-14351",
        "django__django-15022",
        "django__django-15128",
        "django__django-15280",
        "django__django-16263",
    ]
    
    all_to_rerun = errored + container_errored
    agent = "pi"
    timeout = 3600
    
    print(f"=== Re-running {len(all_to_rerun)} instances for agent '{agent}' ===")
    print(f"Errored: {errored}")
    print(f"Container errors: {container_errored}")
    print()
    
    # Remove old output directories to force clean re-run
    output_base = config.output_dir / agent
    for iid in all_to_rerun:
        instance_dir = output_base / iid
        if instance_dir.exists():
            import shutil
            shutil.rmtree(instance_dir)
            print(f"Removed old output: {instance_dir}")
    
    # Run each instance
    for idx, iid in enumerate(all_to_rerun, 1):
        print(f"\n{'='*60}")
        print(f"[{idx}/{len(all_to_rerun)}] Running {iid}...")
        print(f"{'='*60}")
        
        try:
            result = runner.run_instance(agent=agent, instance_id=iid, timeout=timeout)
            print(f"Result: {result}")
        except Exception as e:
            print(f"ERROR running {iid}: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n=== All re-runs complete ===")
    print(f"Run evaluation with: runner.eval('{agent}') or ./run.sh eval {agent}")

if __name__ == "__main__":
    main()
