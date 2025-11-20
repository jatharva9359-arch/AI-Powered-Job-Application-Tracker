import json
import psycopg2

# --- Step 1: PostgreSQL base connection (connect to default 'postgres' DB first) ---

base_config = {
    "host": "localhost",
    "user": "postgres",          # <-- change if needed
    "password": "2545",# <-- your PostgreSQL password
    "dbname": "postgres"         # <-- connect to default DB first
}

DB_NAME = "email_data"
TABLE_NAME = "job_application"

# Connect without target DB first (Postgres requires existing DB)
conn = psycopg2.connect(**base_config)
conn.autocommit = True
cursor = conn.cursor()

# --- Step 2: Create database if it doesn't exist ---
cursor.execute(f"SELECT 1 FROM pg_database WHERE datname='{DB_NAME}'")
exists = cursor.fetchone()

if not exists:
    cursor.execute(f"CREATE DATABASE {DB_NAME}")
    print(f"✅ Database `{DB_NAME}` created.")
else:
    print(f"ℹ️ Database `{DB_NAME}` already exists.")

cursor.close()
conn.close()

# --- Step 3: Connect to the new database ---
db_conn = psycopg2.connect(
    host="localhost",
    user="postgres",
    password="yourPGpassword",
    dbname=DB_NAME
)
db_conn.autocommit = True
db_cursor = db_conn.cursor()

# --- Step 4: Create table if it doesn't exist ---
db_cursor.execute(f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id SERIAL PRIMARY KEY,
    company_name VARCHAR(255),
    date_applied DATE,
    days_since_update INT,
    role_applied_for VARCHAR(255),
    status VARCHAR(100)
)
""")
print(f"✅ Table `{TABLE_NAME}` ensured.")

# --- Step 5: Load JSON data ---
with open("email_applications.json", "r") as f:
    data = json.load(f)

# --- Step 6: Insert data ---
insert_query = f"""
INSERT INTO {TABLE_NAME}
(company_name, date_applied, days_since_update, role_applied_for, status)
VALUES (%s, %s, %s, %s, %s)
"""

for row in data:
    db_cursor.execute(insert_query, (
        row["company_name"],
        row["date_applied"],
        row["days_since_update"],
        row["role_applied_for"],
        row["status"]
    ))

db_conn.commit()
db_cursor.close()
db_conn.close()

print("✅ All data inserted into PostgreSQL successfully.")
