import sqlite3

c = sqlite3.connect("backend_app.db")
print(c.execute("SELECT id, email, role, is_admin FROM users WHERE email LIKE 'qa.admin.%' ORDER BY id DESC LIMIT 4").fetchall())
print("rooms:", c.execute("SELECT count(*) FROM rooms").fetchone()[0])
