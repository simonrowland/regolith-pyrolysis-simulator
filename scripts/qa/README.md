# Report viewer browser QA

Provision the same Python interpreter used to run the simulator:

```bash
python -m pip install -r scripts/qa/requirements-report-viewer.txt
python -m playwright install chromium
```

Start the worktree app on port 8490, then run:

```bash
python scripts/qa/report_viewer_qa.py --round 1 full
```

Evidence is written below the gitignored `instance/qa-report-viewer/` directory.
