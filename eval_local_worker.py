#!/usr/bin/env python3
"""
Lightweight per-instance evaluator for SWE-bench patches.

Runs on the host (no Docker needed). For one instance it:
  1. clones the repo at base_commit (reusing workspace/repos if present)
  2. applies the collected model patch (patch.diff)
  3. applies the dataset's test_patch (ignored if it conflicts, e.g. the
     model already added the test)
  4. creates a venv with a suitable Python and `pip install -e .`
  5. runs the instance's FAIL_TO_PASS + PASS_TO_PASS tests with pytest
  6. writes local_eval.json into the instance output dir

This is a functional stand-in for the full swebench harness eval, which
requires pulling prebuilt eval images (unavailable in some environments).
"""
import json
import os
import re
import subprocess
import sys
import tempfile


def run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def normalize_test_ids(ids):
    """Convert SWE-bench test ids to pytest node ids.

    Some instances use pytest-style ids already (path::Class::test), others use
    the SWE-bench form 'test_name (module.path.Class)'. Normalize the latter.
    """
    out = []
    for t in ids:
        if "::" in t:
            out.append(t)
            continue
        m = re.match(r"^(.*?)\s*\((.*?)\)$", t)
        if m:
            name = m.group(1).strip()
            modpath = m.group(2).strip()
            if "." in modpath:
                parts = modpath.split(".")
                cls = parts[-1]
                mod = ".".join(parts[:-1])
                modfile = mod.replace(".", "/") + ".py"
                out.append(f"{modfile}::{cls}::{name}")
            else:
                out.append(f"{modpath}::{name}")
        else:
            out.append(t)
    return out


def main():
    # Allow git to access repos regardless of ownership
    run(["git", "config", "--global", "--add", "safe.directory", "*"], check=False)
    out = sys.argv[1]
    inp = json.load(open(os.path.join(out, "eval_local_input.json")))
    repo = inp["repo"]
    base = inp["base_commit"]
    ftps = normalize_test_ids(inp.get("FAIL_TO_PASS", []) or [])
    ptps = normalize_test_ids(inp.get("PASS_TO_PASS", []) or [])
    testpatch = inp.get("test_patch", "") or ""

    # Determine workspace/repos directory — try common locations
    repo_dir_candidates = [
        os.path.join(os.environ.get("SWE_WORKSPACE_DIR", "."), "repos", repo),
        os.path.join("/workspace/repos", repo),
    ]
    repodir = None
    for candidate in repo_dir_candidates:
        if os.path.isdir(candidate):
            repodir = candidate
            break

    if not os.path.isdir(repodir):
        run(["git", "clone", "https://github.com/%s.git" % repo, repodir], check=True)
    run(["git", "-C", repodir, "checkout", "-f", base], check=True)
    run(["git", "-C", repodir, "clean", "-fdx"], check=False)

    # Apply model patch (the agent's changes)
    mp = os.path.join(out, "patch.diff")
    applied_model = False
    if os.path.getsize(mp) > 0:
        if run(["git", "-C", repodir, "apply", mp]).returncode == 0:
            applied_model = True
        elif run(["git", "-C", repodir, "apply", "--3way", mp]).returncode == 0:
            applied_model = True

    # Apply gold test patch (ignore failure: model may already include the test)
    if testpatch.strip():
        tf = tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False)
        tf.write(testpatch)
        tf.close()
        run(["git", "-C", repodir, "apply", tf.name], check=False)

    # Pick a working Python — try pyenv first, then system Python
    candidates_pyenv = ["3.8.20", "3.7.17", "3.6.15", "3.9.23", "3.10.16", "3.11.11", "3.5.10"]
    py = None
    venv = None
    for v in candidates_pyenv:
        p = "/opt/pyenv/versions/%s/bin/python3" % v
        if os.path.exists(p):
            venv = tempfile.mkdtemp()
            if run([p, "-m", "venv", venv]).returncode == 0:
                py = p
                break
            venv = None
    # Fallback to system Python
    if not py:
        for sys_py in ["python3.10", "python3.9", "python3.8", "python3"]:
            venv = tempfile.mkdtemp()
            if run([sys_py, "-m", "venv", venv]).returncode == 0:
                py = sys_py
                break
            venv = None
    if not py:
        json.dump({"status": "error", "error": "no suitable python"},
                  open(os.path.join(out, "local_eval.json"), "w"), indent=2)
        sys.exit(0)

    vpy = os.path.join(venv, "bin", "python")
    pip = os.path.join(venv, "bin", "pip")
    install_ok = False
    install_err = None
    run([pip, "install", "Cython"], check=False)
    try:
        run([pip, "install", "-e", repodir, "--no-build-isolation"], check=True, timeout=1500)
        install_ok = True
    except Exception as e:  # noqa: BLE001
        # Fallback for old projects (e.g. sklearn 0.22) that need era-appropriate
        # build deps to compile from source.
        try:
            run([pip, "install", "numpy<1.20", "scipy<1.6", "Cython<3"], check=True, timeout=600)
            run([pip, "install", "-e", repodir, "--no-build-isolation"], check=True, timeout=1500)
            install_ok = True
        except Exception as e2:  # noqa: BLE001
            install_err = str(e2)
    run([pip, "install", "pytest"], check=False)

    tests = list(ftps) + list(ptps)
    result = {}
    if install_ok and tests:
        if repo == "django/django":
            # Django tests need the project test runner (it sets up a test DB);
            # raw pytest cannot run them. Use tests/runtests.py with dotted labels.
            labels = []
            for t in tests:
                m = re.match(r"^(.*?)::(.*?)::(.*)$", t)
                if m:
                    module_file, cls, method = m.group(1), m.group(2), m.group(3)
                    module = module_file[:-3].replace("/", ".")
                    labels.append(f"{module}.{cls}.{method}")
                else:
                    labels.append(t)
            try:
                r = run([vpy, "tests/runtests.py"] + labels, cwd=repodir,
                        capture_output=True, text=True, timeout=1500)
                txt = (r.stdout or "") + "\n" + (r.stderr or "")
                ok = ("OK" in txt) and ("FAILED" not in txt)
                for t in tests:
                    result[t] = "PASSED" if ok else "FAILED"
            except Exception as e:  # noqa: BLE001
                result["_error"] = str(e)
                for t in tests:
                    result[t] = "ERROR"
        else:
            try:
                r = run([vpy, "-m", "pytest", "-v"] + tests, cwd=repodir,
                        capture_output=True, text=True, timeout=1500)
                txt = (r.stdout or "") + "\n" + (r.stderr or "")
                for t in tests:
                    m = re.search(re.escape(t) + r"\s+(PASSED|FAILED|ERROR|SKIPPED)", txt)
                    result[t] = m.group(1) if m else "UNKNOWN"
            except Exception as e:  # noqa: BLE001
                result["_error"] = str(e)
        ftp = [result.get(t, "UNKNOWN") == "PASSED" for t in ftps]
        ptp = [result.get(t, "UNKNOWN") == "PASSED" for t in ptps] if ptps else []
        resolved = (all(ftp) if ftp else False) and (all(ptp) if ptp else True)
        status = "resolved" if resolved else "failed"
    else:
        status = "error" if not install_ok else "failed"

    final = {
        "status": status,
        "install_ok": install_ok,
        "install_error": install_err,
        "model_patch_applied": applied_model,
        "tests": result,
        "FAIL_TO_PASS": ftps,
        "PASS_TO_PASS": ptps,
    }
    open(os.path.join(out, "local_eval.json"), "w").write(json.dumps(final, indent=2))
    print("LOCAL_EVAL", status, "model_patch_applied=", applied_model, "install_ok=", install_ok)


if __name__ == "__main__":
    main()
