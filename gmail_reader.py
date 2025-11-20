"""
Gmail -> HuggingFace -> PostgreSQL
- Reads unread Gmail messages (limit configurable)
- Sends email body to a Hugging Face model that returns JSON
- Parses JSON and inserts into Postgres
- Marks emails as read after processing
"""

from __future__ import print_function
import os
import base64
import json
from email import message_from_bytes
from datetime import datetime
import requests
import psycopg2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------- CONFIG ----------
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']  # need modify to mark read
HUGGING_FACE_API_URL = "https://api-inference.huggingface.co/models/philschmid/bart-large-cnn-samsum"
HUGGING_FACE_API_TOKEN = os.getenv("HUGGING_FACE_API_TOKEN")  # REQUIRED

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")
PG_DB = os.getenv("PG_DB", "email_data")
TABLE_NAME = "job_application"
PROCESS_LIMIT = 5  # how many unread messages to process at once

if not HUGGING_FACE_API_TOKEN:
    raise SystemExit("Set HUGGING_FACE_API_TOKEN environment variable before running.")

# ---------- Gmail helpers ----------
def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                raise SystemExit("Missing credentials.json (GCP OAuth client) in working dir.")
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def get_unread_message_ids(service, max_results=PROCESS_LIMIT):
    resp = service.users().messages().list(userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=max_results).execute()
    return [m['id'] for m in resp.get('messages', [])]

def get_message_raw(service, msg_id):
    msg = service.users().messages().get(userId='me', id=msg_id, format='raw').execute()
    raw = base64.urlsafe_b64decode(msg['raw'].encode('ASCII'))
    return raw

def mark_message_read(service, msg_id):
    service.users().messages().modify(userId='me', id=msg_id, body={'removeLabelIds': ['UNREAD']}).execute()

# ---------- Hugging Face helpers ----------
def analyze_email_with_hf(email_text):
    """
    Sends a prompt to the HF inference endpoint that requests a JSON response.
    Returns parsed JSON or raw text on failure.
    """
    prompt = f"""
You are an AI assistant that extracts structured job application information from emails.

Extract the following fields in JSON format (exact keys):
- type: "confirmation" | "update" | "other"
- company: Company name mentioned or empty string
- role: Job title mentioned or empty string
- date: Date of application or update in YYYY-MM-DD or empty string

Respond ONLY with the JSON object, nothing else.

Email:
\"\"\"{email_text}\"\"\"
"""
    headers = {"Authorization": f"Bearer {HUGGING_FACE_API_TOKEN}", "Content-Type": "application/json"}
    payload = {"inputs": prompt, "options": {"wait_for_model": True}}

    r = requests.post(HUGGING_FACE_API_URL, headers=headers, json=payload, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Hugging Face error {r.status_code}: {r.text}")

    # HF sometimes returns a list with dicts or a dict with generated_text/summary_text
    result = r.json()
    text = None
    if isinstance(result, list) and result and isinstance(result[0], dict):
        text = result[0].get("generated_text") or result[0].get("summary_text") or str(result[0])
    elif isinstance(result, dict):
        # some HF endpoints return {'generated_text': '...'}
        text = result.get("generated_text") or result.get("summary_text") or json.dumps(result)
    else:
        text = str(result)

    # Try to extract JSON block from text
    try:
        # find first { ... } in text
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            json_text = text[start:end+1]
            parsed = json.loads(json_text)
            return parsed
    except Exception:
        pass

    # last resort: try to parse any json content
    try:
        return json.loads(text)
    except Exception:
        return {"type": "other", "company": "", "role": "", "date": "" , "raw": text}

# ---------- Postgres helpers ----------
def init_postgres():
    # connect to default postgres to ensure DB exists, then create table
    tmp_conn = psycopg2.connect(host=PG_HOST, user=PG_USER, password=PG_PASSWORD, dbname="postgres")
    tmp_conn.autocommit = True
    cur = tmp_conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s;", (PG_DB,))
    if not cur.fetchone():
        cur.execute(f"CREATE DATABASE {PG_DB};")
        print(f"✅ Created database {PG_DB}")
    cur.close()
    tmp_conn.close()

    conn = psycopg2.connect(host=PG_HOST, user=PG_USER, password=PG_PASSWORD, dbname=PG_DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id SERIAL PRIMARY KEY,
            msg_id TEXT UNIQUE,
            email_from TEXT,
            subject TEXT,
            extracted_type TEXT,
            company TEXT,
            role TEXT,
            extracted_date DATE,
            processed_at TIMESTAMP DEFAULT now(),
            raw_ai_output JSONB
        );
    """)
    cur.close()
    return conn

def insert_extraction(conn, msg_id, email_from, subject, parsed_json):
    cur = conn.cursor()
    # normalize parsed fields
    ex_type = parsed_json.get("type") or parsed_json.get("status") or "other"
    company = parsed_json.get("company") or parsed_json.get("company_name") or ""
    role = parsed_json.get("role") or parsed_json.get("role_applied_for") or ""
    date_str = parsed_json.get("date") or parsed_json.get("date_applied") or ""
    extracted_date = None
    if date_str:
        try:
            extracted_date = datetime.fromisoformat(date_str).date()
        except Exception:
            extracted_date = None

    cur.execute(f"""
        INSERT INTO {TABLE_NAME} (msg_id, email_from, subject, extracted_type, company, role, extracted_date, raw_ai_output)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (msg_id) DO NOTHING;
    """, (msg_id, email_from, subject, ex_type, company, role, extracted_date, json.dumps(parsed_json)))
    conn.commit()
    cur.close()

# ---------- Main ----------
def process_unread():
    service = get_gmail_service()
    conn = init_postgres()
    msg_ids = get_unread_message_ids(service, PROCESS_LIMIT)
    if not msg_ids:
        print("No unread messages.")
        return

    for msg_id in msg_ids:
        try:
            raw = get_message_raw(service, msg_id)
            mime = message_from_bytes(raw)
            subject = mime.get('Subject', '')
            sender = mime.get('From', '')

            # extract text body (plain or html fallback)
            body = ""
            if mime.is_multipart():
                for part in mime.walk():
                    ctype = part.get_content_type()
                    if ctype == "text/plain":
                        body += part.get_payload(decode=True).decode(errors='ignore')
                    elif ctype == "text/html" and not body:
                        body += part.get_payload(decode=True).decode(errors='ignore')
            else:
                body = mime.get_payload(decode=True).decode(errors='ignore')

            print(f"\nProcessing msg {msg_id} | From: {sender} | Subject: {subject[:80]}")

            # call HF
            parsed = analyze_email_with_hf(body[:2000])  # limit length to avoid huge prompts
            print("AI parsed:", parsed)

            # insert into Postgres
            insert_extraction(conn, msg_id, sender, subject, parsed)

            # mark as read
            mark_message_read(service, msg_id)
            print("Marked as read and inserted into DB.")

        except Exception as ex:
            print("Error processing", msg_id, ex)

    conn.close()

if __name__ == "__main__":
    process_unread()
