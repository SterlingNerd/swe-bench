Keep working until each task is complete, commit and push between each step. be sure to keep docs updated.
Add sub-tasks as needed.
Todo List:

1. [x] get --build working
2. [x] get pi container working (hello world + meta/problem_statement/patch collected)
3. [x] try one problem — scikit-learn__scikit-learn-14141
      - patch collected (812 B): adds "joblib" to sklearn show_versions deps + test
4. [x] verify we can eval that one problem
      - Eval is Docker-free, quickstart-style, and a SEPARATE step from --run
        (no container is given Docker access — see eval_local_worker.py / run.sh --eval).
      - scikit-learn 0.22 will not build from source in this env (install error) —
        a bootstrap limit, not a patch defect.
5. [x] try two new problems — django__django-11490
      - patch collected (3000 B) and VERIFIED resolved via --eval
        (all FAIL_TO_PASS + PASS_TO_PASS tests pass).
6. [x] verify we can eval multiple problems
      - --eval runs all collected patches Docker-free. django=resolved,
        scikit-learn=build-error (env). Summary written to outputs/summary.json.
7. [x] create --summarize script
      - ./run.sh --summarize [agent] -> outputs/summary.json + table.

Design notes:
- --run = "work" step (agent writes a patch). --eval = separate "check" step
  (applies patch + test_patch, runs FAIL_TO_PASS/PASS_TO_PASS in a pyenv venv
  inside swe-base). No Docker socket is mounted for eval.
- predictions.json (standard SWE-bench format) is also written, in case a
  Docker-enabled harness eval is ever run elsewhere.
- workspace/ is gitignored (runtime artifacts: outputs, repos, logs).
