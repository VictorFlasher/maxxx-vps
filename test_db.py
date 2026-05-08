import os
import psycopg2

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASS", "postgres"),
}

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()

# Проверяем схему
cur.execute("""
    SELECT schemaname, tablename 
    FROM pg_tables 
    WHERE tablename IN ('users', 'messages', 'message_reports')
    ORDER BY schemaname, tablename
""")
print("Таблицы:")
for row in cur.fetchall():
    print(f"  {row[0]}.{row[1]}")

# Проверяем структуру messages
print("\nСтруктура messages:")
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_schema = 'maxxx_local' AND table_name = 'messages'
    ORDER BY ordinal_position
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

# Проверяем структуру message_reports
print("\nСтруктура message_reports:")
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_schema = 'maxxx_local' AND table_name = 'message_reports'
    ORDER BY ordinal_position
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

cur.close()
conn.close()
