import json

with open("qa_logs/qa_live_rooms_result.json", encoding="utf-16") as handle:
    data = json.load(handle)
for case in data["cases"]:
    print(case["id"], "|", case["status"], "|", case["actual"][:100])
