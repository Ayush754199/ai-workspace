import json

text = open("qa_logs/qa_live_rooms_mongo.txt", encoding="utf-16", errors="ignore").read()
text = text[text.index("{"):]
data = json.loads(text)
print("label sample:", data["cases"][0]["status"])
for case in data["cases"]:
    print(f'{case["id"]:22} {case["status"]:24} {case["actual"][:70]}')
