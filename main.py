from datetime import datetime
import os
import sqlite3
import atexit

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr

from fastapi import Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


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
    raise RuntimeError("Missing GMAIL_USER or GMAIL_APP_PASSWORD in .env")

yag = yagmail.SMTP(GMAIL_USER, GMAIL_APP_PASSWORD)

def send_email(to_email: str, subject: str, body: str):
    yag.send(to=to_email, subject=subject, contents=body)

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
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

db_init()

def get_all_users():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT email, timezone, alert_thu, alert_sat, alert_sun, alert_waiver
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
        })
    return users

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
# FastAPI app
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

@app.get("/")
def home():
    return {"status": "FantasyFrenzy Alerts running", "time": str(datetime.now())}

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

@app.get("/landing", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/signup-web", response_class=HTMLResponse)
def signup_web(
    request: Request,
    email: str = Form(...),
    timezone: str = Form("America/New_York"),
    alert_thu: str | None = Form(None),
    alert_sat: str | None = Form(None),
    alert_sun: str | None = Form(None),
    alert_waiver: str | None = Form(None),
):
    
 from fastapi.responses import RedirectResponse

@app.get("/")
def root():
    return RedirectResponse(url="/landing")
   
    # HTML checkboxes send "on" when checked, nothing when unchecked
    upsert_user(
        email=email,
        timezone=timezone,
        alert_thu=bool(alert_thu),
        alert_sat=bool(alert_sat),
        alert_sun=bool(alert_sun),
        alert_waiver=bool(alert_waiver),
    )
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "message": f"✅ You’re signed up, {email}!"}
    )


# ----------------------------
# Reminder logic
# ----------------------------
def send_to_matching_users(alert_key: str, subject: str, body: str):
    users = get_all_users()
    sent = 0
    for u in users:
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
# Manual test endpoint
# ----------------------------
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
