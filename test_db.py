
import sqlite3, os
db_path = 'database/snapshots/localhost.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM opname_snapshot')
        print('COUNT:', cur.fetchone()[0])
    except Exception as e:
        print('ERROR:', e)

