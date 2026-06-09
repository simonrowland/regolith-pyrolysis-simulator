#!/usr/bin/env python3
"""Grind health probe for a studio node. Reports proc liveness, CPU, cache row
counts, domain_gaps, and the last log line. Run on the node via ssh."""
import json
import os
import sqlite3
import subprocess
import sys

HOME = os.path.expanduser("~")
DB = os.path.join(HOME, "cache-grind", "blitz.db")
JSON = os.path.join(HOME, "cache-grind", "blitz.json")
LOG = os.path.join(HOME, "cache-grind", "blitz.log")


def procs():
    try:
        out = subprocess.run(
            ["pgrep", "-f", "populate_reduced_real_cache"],
            capture_output=True, text=True,
        ).stdout.split()
    except Exception:
        out = []
    rows = []
    for pid in out:
        cpu = subprocess.run(
            ["ps", "-o", "%cpu=", "-p", pid], capture_output=True, text=True
        ).stdout.strip()
        rows.append(f"pid={pid} cpu={cpu}%")
    return rows


def cache_counts():
    if not os.path.exists(DB):
        return "db: not-created-yet"
    con = sqlite3.connect(DB)
    tabs = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in tabs}
    return "cache: " + json.dumps(counts)


def gaps_and_status():
    if not os.path.exists(JSON):
        return "json: none-yet"
    try:
        d = json.load(open(JSON))
    except Exception as exc:
        return f"json: unreadable ({exc})"
    return (f"status={d.get('status')} "
            f"domain_gaps={d.get('domain_gaps', '<no-key>')} "
            f"live_cases={len(d.get('live', []))}")


def last_log():
    if not os.path.exists(LOG):
        return "log: none"
    lines = open(LOG, errors="replace").read().splitlines()
    tail = [ln for ln in lines if ln.strip()][-2:]
    return "log_tail: " + " || ".join(tail) if tail else "log: empty"


if __name__ == "__main__":
    p = procs()
    print("ALIVE " + "; ".join(p) if p else "NOT RUNNING")
    print(cache_counts())
    print(gaps_and_status())
    print(last_log())
