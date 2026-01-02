from datetime import datetime
import email
import os
import sqlite3
import atexit
import urllib.parse

import hmac
import hashlib

import hmac
import hashlib
import urllib.parse

from fastapi import FastAPI, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr

import yagmail
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

# ----------------------------
# ENV + Email setup
# ----------------------------
load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

if not GMAIL_USER or not GMAIL_APP_PASSWORD:
    raise RuntimeError("Missing GMAIL_USER or GMAIL_APP_PASSWORD in environment variables")

yag = yagmail.SMTP(GMAIL_USER, GMAIL_APP_PASSWORD)

def send_email(to_email: str, subject: str, body: str):
    yag.send(to=to_email, subject=subject, contents=body)

def send_welcome_email(to_email: str):
    subject = "FantasyFrenzy ✅ You’re signed up"
    body = (
        "You’re all set!\n\n"
        "FantasyFrenzy will remind you before key fantasy football moments:\n"
        "• Thursday Night Football\n"
        "• Saturday games\n"
        "• Sunday morning kickoffs\n"
        "• Waiver deadlines\n\n"
        "You don’t need to do anything else.\n\n"
        "Tip: If you don’t see future emails, check spam and mark as Not Spam.\n\n"
        "Good luck this week 🏈\n"
        "– FantasyFrenzy"
    )
    send_email(to_email, subject, body)

# ----------------------------
# Database (SQLite)
# ----------------------------
DB_PATH = "ffalerts.db"

def db_connect():
    return sqlite3.connect(DB_PATH)

def db_init():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            timezone TEXT NOT NULL,
            alert_thu INTEGER NOT NULL DEFAULT 1,
            alert_sat INTEGER NOT NULL DEFAULT 1,
            alert_sun INTEGER NOT NULL DEFAULT 1,
            alert_waiver INTEGER NOT NULL DEFAULT 1,
            unsubscribed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

db_init()

def db_migrate():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(users);")
    cols = [row[1] for row in cur.fetchall()]
    if "unsubscribed" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN unsubscribed INTEGER NOT NULL DEFAULT 0;")
        conn.commit()
    conn.close()

db_migrate()


def get_all_users():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT email, timezone, alert_thu, alert_sat, alert_sun, alert_waiver, unsubscribed
FROM users
    """)
    rows = cur.fetchall()
    conn.close()

    users = []
    for r in rows:
        users.append({
            "email": r[0],
            "timezone": r[1],
            "alert_thu": bool(r[2]),
            "alert_sat": bool(r[3]),
            "alert_sun": bool(r[4]),
            "alert_waiver": bool(r[5]),
            "unsubscribed": bool(r[6]),
        })
    return users

def get_user(email: str):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT email, timezone, alert_thu, alert_sat, alert_sun, alert_waiver, unsubscribed
        FROM users
        WHERE email = ?
    """, (email,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "email": row[0],
        "timezone": row[1],
        "alert_thu": bool(row[2]),
        "alert_sat": bool(row[3]),
        "alert_sun": bool(row[4]),
        "alert_waiver": bool(row[5]),
        "unsubscribed": bool(row[6]),
    }


def set_unsubscribed(email: str, unsubscribed: bool = True):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET unsubscribed = ? WHERE email = ?",
        (1 if unsubscribed else 0, email)
    )
    conn.commit()
    conn.close()


def upsert_user(email: str, timezone: str, alert_thu=True, alert_sat=True, alert_sun=True, alert_waiver=True):
    conn = db_connect()
    cur = conn.cursor()
    now = datetime.now().isoformat()

    cur.execute("""
        INSERT INTO users (email, timezone, alert_thu, alert_sat, alert_sun, alert_waiver, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            timezone=excluded.timezone,
            alert_thu=excluded.alert_thu,
            alert_sat=excluded.alert_sat,
            alert_sun=excluded.alert_sun,
            alert_waiver=excluded.alert_waiver;
    """, (
        email,
        timezone,
        1 if alert_thu else 0,
        1 if alert_sat else 0,
        1 if alert_sun else 0,
        1 if alert_waiver else 0,
        now
    ))

    conn.commit()
    conn.close()

# ----------------------------
# FastAPI app + Templates
# ----------------------------
app = FastAPI()
templates = Jinja2Templates(directory="templates")

class SignupRequest(BaseModel):
    email: EmailStr
    timezone: str = "America/New_York"
    alert_thu: bool = True
    alert_sat: bool = True
    alert_sun: bool = True
    alert_waiver: bool = True

# ----------------------------
# API endpoints
# ----------------------------
@app.post("/signup")
def signup(payload: SignupRequest):
    if "/" not in payload.timezone:
        raise HTTPException(status_code=400, detail="Invalid timezone")

    upsert_user(
        email=payload.email,
        timezone=payload.timezone,
        alert_thu=payload.alert_thu,
        alert_sat=payload.alert_sat,
        alert_sun=payload.alert_sun,
        alert_waiver=payload.alert_waiver,
    )
    return {"ok": True, "saved": payload.email}

@app.get("/users")
def list_users():
    return {"count": len(get_all_users()), "users": get_all_users()}

# ----------------------------
# Web pages
# ----------------------------
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/landing")

@app.get("/landing", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/confirmed", response_class=HTMLResponse)
def confirmed(
    request: Request,
    email: str = Query(""),
    timezone: str = Query("America/New_York"),
    alert_thu: bool = Query(True),
    alert_sat: bool = Query(True),
    alert_sun: bool = Query(True),
    alert_waiver: bool = Query(True),
):
    return templates.TemplateResponse(
        "confirmed.html",
        {
            "request": request,
            "email": email,
            "timezone": timezone,
            "alert_thu": alert_thu,
            "alert_sat": alert_sat,
            "alert_sun": alert_sun,
            "alert_waiver": alert_waiver,
        },
    )

@app.post("/signup-web")
def signup_web(
    request: Request,
    email: str = Form(...),
    timezone: str = Form("America/New_York"),
    alert_thu: str | None = Form(None),
    alert_sat: str | None = Form(None),
    alert_sun: str | None = Form(None),
    alert_waiver: str | None = Form(None),
):
    # Save user settings
    upsert_user(
        email=email,
        timezone=timezone,
        alert_thu=bool(alert_thu),
        alert_sat=bool(alert_sat),
        alert_sun=bool(alert_sun),
        alert_waiver=bool(alert_waiver),
    )
    send_welcome_email(email)   

    SIGNING_SECRET = os.getenv("SIGNING_SECRET", "")

def make_token(email: str) -> str:
    if not SIGNING_SECRET:
        raise RuntimeError("Missing SIGNING_SECRET env var")
    return hmac.new(
        SIGNING_SECRET.encode("utf-8"),
        email.lower().encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def verify_token(email: str, token: str) -> bool:
    try:
        expected = make_token(email)
        return hmac.compare_digest(expected, token)
    except Exception:
        return False



    # Build redirect URL safely (handles @ and special chars)
    params = {
        "email": email,
        "timezone": timezone,
        "alert_thu": str(bool(alert_thu)).lower(),
        "alert_sat": str(bool(alert_sat)).lower(),
        "alert_sun": str(bool(alert_sun)).lower(),
        "alert_waiver": str(bool(alert_waiver)).lower(),
    }
    url = "/confirmed?" + urllib.parse.urlencode(params)

    return RedirectResponse(url=url, status_code=303)

# ----------------------------
# Reminder logic
# ----------------------------
def send_to_matching_users(alert_key: str, subject: str, body: str):
    users = get_all_users()
    sent = 0

    for u in users:
        # Skip unsubscribed users
        if u.get("unsubscribed"):
            continue

        if u.get(alert_key):
            send_email(u["email"], subject, body)
            sent += 1

    print(f"[{datetime.now()}] Sent '{alert_key}' to {sent} users")

def thursday_reminder():
    send_to_matching_users(
        "alert_thu",
        "FantasyFrenzy 🔔 Thursday Night Lineup Reminder",
        "TNF is coming up. Set your lineup now."
    )

def saturday_reminder():
    send_to_matching_users(
        "alert_sat",
        "FantasyFrenzy 🔔 Saturday Lineup Reminder",
        "Saturday games are coming up."
    )

def sunday_morning_reminder():
    send_to_matching_users(
        "alert_sun",
        "FantasyFrenzy 🔔 Sunday Morning Lineup Reminder",
        "Final lineup check before kickoffs."
    )

def waiver_reminder():
    send_to_matching_users(
        "alert_waiver",
        "FantasyFrenzy 🔔 Waiver Reminder",
        "Waivers process tonight. Submit claims."
    )

# ----------------------------
# Scheduler
# ----------------------------
scheduler = BackgroundScheduler(timezone="America/New_York")
scheduler.add_job(thursday_reminder, "cron", day_of_week="thu", hour=18, minute=0)
scheduler.add_job(saturday_reminder, "cron", day_of_week="sat", hour=9, minute=0)
scheduler.add_job(sunday_morning_reminder, "cron", day_of_week="sun", hour=9, minute=30)
scheduler.add_job(waiver_reminder, "cron", day_of_week="tue", hour=22, minute=0)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ----------------------------
# Manual test endpoints
# ----------------------------
@app.get("/test-email")
def test_email():
    to_email = os.getenv("TEST_EMAIL_TO")
    if not to_email:
        raise HTTPException(status_code=500, detail="Missing TEST_EMAIL_TO env var")

    send_email(
        to_email,
        "FantasyFrenzy ✅ Test Email",
        "If you got this, your Render deployment can send emails successfully."
    )
    return {"ok": True, "sent_to": to_email}

SECRET_KEY = os.getenv("SECRET_KEY")
BASE_URL = os.getenv("BASE_URL")

def require_unsub_config():
    if not SECRET_KEY or not BASE_URL:
        raise RuntimeError("Missing SECRET_KEY or BASE_URL environment variables")

def make_unsub_token(email: str) -> str:
    require_unsub_config()

    email_norm = email.strip().lower()
    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        email_norm.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def verify_unsub_token(email: str, token: str) -> bool:
    require_unsub_config()

    return hmac.compare_digest(make_unsub_token(email), (token or ""))

def build_unsub_link(email: str) -> str:
    require_unsub_config()

    params = urllib.parse.urlencode({"email": email, "token": make_unsub_token(email)})
    return f"{BASE_URL}/unsubscribe?{params}"


@app.post("/test/{which}")
def test(which: str):
    which = which.lower()
    if which == "thu":
        thursday_reminder()
    elif which == "sat":
        saturday_reminder()
    elif which == "sun":
        sunday_morning_reminder()
    elif which == "waiver":
        waiver_reminder()
    else:
        return {"error": "use thu, sat, sun, waiver"}
    return {"ok": True, "ran": which}
