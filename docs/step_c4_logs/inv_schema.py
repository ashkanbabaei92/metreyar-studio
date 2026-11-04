import sqlite3
from pathlib import Path

db_path = Path("/home/user/webapp/data_processed/reference.db")
uri = f"file:{db_path}?mode=ro"
conn = sqlite3.connect(uri, uri=True)
cur = conn.cursor()

for tbl in ["chapters", "items", "notes", "rules"]:
    print(f"=== {tbl} ===")
    cur.execute(f"PRAGMA table_info({tbl})")
    for row in cur.fetchall():
        print(row)

conn.close()
