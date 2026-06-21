import sqlite3

conn = sqlite3.connect("bot_state.db")
conn.execute("UPDATE state SET value=? WHERE key='last_message_id'", ("3903",))
conn.commit()
print("OK")