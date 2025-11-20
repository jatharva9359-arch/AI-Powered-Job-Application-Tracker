import imaplib
import email
from email.header import decode_header
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime
from transformers import pipeline
import torch
import psycopg2

# --- EMAIL CONFIG ---
EMAIL = "jatharva2507@gmail.com"
APP_PASSWORD = "bfgj ihxg jixb khbn"   # Gmail app password

# --- POSTGRES CONFIG ---
PG_HOST = "localhost"
PG_USER = "postgres"
PG_PASSWORD = "2545"
PG_DB = "email_data"
TABLE_NAME = "job_application"


# --------------------- PostgreSQL Setup ---------------------

def init_postgres():
    """Create database & table if they don't exist."""
    # Connect to default DB first
    conn = psycopg2.connect(host=PG_HOST, user=PG_USER, password=PG_PASSWORD, dbname="postgres")
    conn.autocommit = True
    cursor = conn.cursor()

    cursor.execute(f"SELECT 1 FROM pg_database WHERE datname='{PG_DB}'")
    if not cursor.fetchone():
        cursor.execute(f"CREATE DATABASE {PG_DB}")
        print(f"✅ Database `{PG_DB}` created")
    else:
        print(f"ℹ️ Database `{PG_DB}` already exists")

    cursor.close()
    conn.close()

    # Connect to actual DB
    db_conn = psycopg2.connect(host=PG_HOST, user=PG_USER, password=PG_PASSWORD, dbname=PG_DB)
    db_conn.autocommit = True
    db_cursor = db_conn.cursor()

    # Create table
    db_cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        id SERIAL PRIMARY KEY,
        company_name VARCHAR(255),
        date_applied DATE,
        days_since_update INT,
        role_applied_for VARCHAR(255),
        status VARCHAR(100)
    );
    """)

    print(f"✅ Table `{TABLE_NAME}` ready")

    return db_conn, db_cursor


# --------------------- Gmail Functions ---------------------

def connect_to_gmail():
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(EMAIL, APP_PASSWORD)
    imap.select("inbox")
    return imap

def fetch_emails(imap, num_emails=50):
    status, messages = imap.search(None, 'ALL')
    email_ids = messages[0].split()[-num_emails:]
    emails = []

    for eid in email_ids:
        res, msg_data = imap.fetch(eid, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                subject, encoding = decode_header(msg["Subject"])[0]
                subject = subject.decode(encoding or "utf-8") if isinstance(subject, bytes) else subject
                from_address = msg.get("From")

                match = re.search(r"@([\w.-]+)", from_address)
                sender_domain = match.group(1).split('.')[0].capitalize() if match else "Unknown"

                date_tuple = email.utils.parsedate_tz(msg['Date'])
                received_date = datetime.fromtimestamp(
                    email.utils.mktime_tz(date_tuple)
                ).strftime("%Y-%m-%d")

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        if content_type in ["text/plain", "text/html"]:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode(errors='ignore')
                                break
                else:
                    body = msg.get_payload(decode=True).decode(errors='ignore')

                if "<html" in body:
                    soup = BeautifulSoup(body, "html.parser")
                    body = soup.get_text()

                if is_job_related(subject, body):
                    emails.append({
                        "subject": subject,
                        "body": body,
                        "sender_domain": sender_domain,
                        "date_received": received_date
                    })
    return emails

def is_job_related(subject, body):
    job_keywords = ["job", "application", "interview", "offer", "position", "role", "hiring", "career"]
    subject = subject.lower()
    body = body.lower()
    return any(k in subject or k in body for k in job_keywords)


# --------------------- NLP Pipeline ---------------------

device = 0 if torch.cuda.is_available() else -1
ner_pipeline = pipeline("ner", model="dslim/bert-base-NER", device=device)


def extract_info(email_data):
    text = email_data['body']
    sender_domain = email_data['sender_domain']
    received_date = email_data['date_received']

    entities = ner_pipeline(text)

    company_name = extract_company_name(text) or sender_domain
    role = guess_role(text)
    status = guess_status(text, role)

    return {
        "company_name": company_name,
        "date_applied": received_date,
        "days_since_update": calculate_days_since(received_date),
        "role_applied_for": role,
        "status": status
    }


# --------------------- Helper Functions ---------------------

def extract_company_name(text):
    match = re.search(r"applied to\s+(.*?)(?:\s+at|\.|$)", text, re.IGNORECASE)
    return match.group(1).strip() if match else None

def guess_role(text):
    patterns = [
        r"position of\s+(.*?)(?:\s+at|\.|$)",
        r"role of\s+(.*?)(?:\s+at|\.|$)",
        r"applied for\s+(.*?)(?:\s+at|\.|$)"
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return "Unknown"

def guess_status(text, role):
    text = text.lower()
    if "interview" in text or "scheduled" in text:
        return "Interview Scheduled"
    if "shortlisted" in text:
        return "Shortlisted"
    if "reject" in text or "not selected" in text:
        return "Rejected"
    if "applied" in text:
        return "Applied"
    return "Unknown"

def calculate_days_since(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (datetime.today() - d).days


# --------------------- Main Pipeline ---------------------

def main():
    # Setup PostgreSQL
    pg_conn, pg_cursor = init_postgres()

    # Fetch emails
    imap = connect_to_gmail()
    emails = fetch_emails(imap)

    print(f"\n📨 {len(emails)} job-related emails found.\n")

    for email_data in emails:
        extracted = extract_info(email_data)
        print("✔ Extracted:", extracted)

        # Insert into PostgreSQL
        pg_cursor.execute(f"""
        INSERT INTO {TABLE_NAME}
        (company_name, date_applied, days_since_update, role_applied_for, status)
        VALUES (%s, %s, %s, %s, %s)
        """, (
            extracted["company_name"],
            extracted["date_applied"],
            extracted["days_since_update"],
            extracted["role_applied_for"],
            extracted["status"]
        ))

    pg_conn.commit()
    pg_cursor.close()
    pg_conn.close()

    print("\n🎉 All email data inserted into PostgreSQL!")

    imap.logout()


if __name__ == "__main__":
    main()
