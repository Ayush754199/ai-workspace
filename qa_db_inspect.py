import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "backend/backend_app.db"
c = sqlite3.connect(db)
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("TABLES:", tables)
for table in ("ai_messages", "quota_reservations", "usage_logs", "capability_quotas"):
    if table not in tables:
        continue
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    print(f"\n== {table} ({cols}) ==")
    for row in c.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 8"):
        print(row)
