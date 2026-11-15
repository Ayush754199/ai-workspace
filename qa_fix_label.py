from pathlib import Path

path = Path("qa_live_rooms.py")
text = path.read_text(encoding="utf-8")
text = text.replace('\\"Pass (Live - Mongo QA)\\"', 'LABEL')
text = text.replace('\\"Pass (Live - Mongo QA)\\"', 'LABEL')
# Docstring line that lost its quotes.
text = text.replace('instead of \\"Pass (Live - Mongo QA)\\".', 'instead of "Pass (Live)".')
path.write_text(text, encoding="utf-8")
compile(text, "qa_live_rooms.py", "exec")
print("patched, LABEL uses:", text.count("LABEL"))
