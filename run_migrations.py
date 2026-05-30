# ODIN Migration Runner
import os, psycopg2

DB_URL = "postgresql://postgres:xxYYUJFWAITztvOFxRTpeLWcpYvRXWXc@yamabiko.proxy.rlwy.net:52899/railway"

BASE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS = [
    os.path.join(BASE, "api", "migrations", "001_create_users.sql"),
    os.path.join(BASE, "api", "migrations", "002_seed_data.sql"),
]

conn = psycopg2.connect(DB_URL)
conn.autocommit = True

for path in MIGRATIONS:
    name = os.path.basename(path)
    print(f"\nRunning {name}...")
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        print(f"  OK: {name}")
    except Exception as e:
        print(f"  FAIL: {name} -- {e}")

conn.close()

print("\nVerifying users table...")
conn2 = psycopg2.connect(DB_URL)
with conn2.cursor() as cur:
    cur.execute("SELECT name, email, role FROM users ORDER BY role;")
    rows = cur.fetchall()
    for row in rows:
        print(f"  {row[0]} | {row[1]} | {row[2]}")
conn2.close()
print("\nDone.")
