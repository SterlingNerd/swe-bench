Keep working until each task is complete, commit and push between each step. be sure to keep docs updated.
Add sub-tasks as needed.
Todo List:

1. [x] get --build working
2. [x] get pi container working (have it write a hello world file to output so that we can verify)
3. [ ] try one problem
4. [ ] verify we can eval that one problem
5. [ ] try two new problems
6. [ ] verify we can eval multiple problems
7. [ ] create --summarize script to combine and summarize results (hoo it to swe-bench if swe-bench already has something)
8. [ ] Upgrade pi in the swe-pi image past 0.74.2
   - Symptom: ./run.sh --rebuild pi reinstalls 0.74.2 even though npm
     `latest` is 0.80.6 (0.80.3 also published).
   - Root cause: agents/pi/Dockerfile.pi installs Node.js 20.x, but pi's
     `latest` (0.80.x) requires Node >=22.19.0 (engines.node). On Node 20,
     `npm install -g @earendil-works/pi-coding-agent` resolves to the
     `legacy-node20` dist-tag (0.74.2) instead of `latest`.
   - Fix options (pick one):
       a) Bump Dockerfile.pi to Node >=22.19.0 so `latest` (0.80.x) installs.
       b) Keep Node 20 and explicitly pin to a Node-20-compatible version
          (e.g. @earendil-works/pi-coding-agent@legacy-node20) — but that
          caps at 0.74.2, so only viable if 0.80.x is not required.
       c) Pin an explicit 0.80.x version after moving to Node 22+.
   - Verify after change: ./run.sh --rebuild pi, then
     `docker run --rm --entrypoint sh swe-pi:latest -c "pi --version"`
     should report >= 0.80.x.
   - Note: 0.80.3 reference in agents/pi/.pi/settings.json is just the
     changelog-seen marker (lastChangelogVersion), NOT the installed CLI.
