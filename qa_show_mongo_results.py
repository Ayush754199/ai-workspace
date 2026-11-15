import json
from pathlib import Path

raw = Path("qa_logs/qa_mongo_suite_out.txt").read_bytes().decode("utf-16", errors="replace")
data = json.loads(raw[raw.index("{"):])
counts = {}
failures = []
for case in data["cases"]:
    counts[case["status"]] = counts.get(case["status"], 0) + 1
    if case["status"] not in {"Pass (Live - Mongo QA)"}:
        failures.append(case)
print("run:", data["run"], "| counts:", counts)
for case in failures:
    print("  NOT PASS:", case["id"], "|", case["status"], "|", case["actual"][:110])
