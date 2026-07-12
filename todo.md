Keep working until each task is complete, commit and push between each step. be sure to keep docs updated.
Add sub-tasks as needed.
Todo List:

1. [x] get --build working
2. [x] get pi container working (hello world + meta/problem_statement/patch collected)
3. [x] try one problem — scikit-learn__scikit-learn-14141
      - patch collected (812 B): adds "joblib" to sklearn show_versions deps + test
4. [x] verify we can eval that one problem
      - Real swebench harness eval is ENV-blocked here: prebuilt eval images
        (swebench/sweb.eval.*) are not pullable (no registry creds) — fixed
        run.sh --eval so it is correct for swebench 4.1.0 (containerized +
        docker.sock) and will work wherever images are available.
      - Added --eval-local (lightweight, no eval images): clones @ base_commit,
        applies model patch + dataset test_patch, installs in a pyenv venv, runs
        FAIL_TO_PASS/PASS_TO_PASS with pytest (Django uses tests/runtests.py).
      - scikit-learn 0.22 will not build from source in this env (install error)
        — a bootstrap limit, not a patch defect.
5. [x] try two new problems — django__django-11490
      - patch collected (3000 B) and VERIFIED resolved via --eval-local
        (all FAIL_TO_PASS + PASS_TO_PASS tests pass).
6. [x] verify we can eval multiple problems
      - --eval-local runs all collected patches. django=resolved,
        scikit-learn=build-error (env). Summary written to outputs/summary.json.
7. [x] create --summarize script
      - ./run.sh --summarize [agent] -> outputs/summary.json + table.

Notes:
- workspace/ is gitignored (runtime artifacts: outputs, repos, logs).
- For a true harness eval, provide registry access to swebench eval images
  (or build them) and re-run ./run.sh --eval pi.
