import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request
from google.cloud import firestore

app = Flask(__name__, static_folder="css", static_url_path="/css")

# Initialize Firestore Client
db = firestore.Client()

# Core SMTP Email Configurations (Reads from Environment Variables)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USERNAME)
CHECK_IN_WINDOW_HOURS = 24


def check_in_view_data(last_check_in=None):
    """Build the timestamps and labels used by the dashboard."""
    now = datetime.now(timezone.utc)
    if last_check_in is not None and last_check_in.tzinfo is None:
        last_check_in = last_check_in.replace(tzinfo=timezone.utc)

    window_start = last_check_in or now
    deadline = window_start.timestamp() + (CHECK_IN_WINDOW_HOURS * 60 * 60)
    deadline = datetime.fromtimestamp(deadline, timezone.utc)

    return {
        "current_date": now.strftime("%A, %B %d, %Y").replace(" 0", " "),
        "last_check_in_label": (
            last_check_in.astimezone().strftime("%b %d, %Y at %I:%M %p")
            .replace(" 0", " ")
            if last_check_in
            else "Not yet"
        ),
        "check_in_deadline": deadline.isoformat(),
    }

def send_alert_email(to_email, subject, body_text):
    """Securely handles SMTP email dispatch loops."""
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print(f"Skipping email to {to_email}: SMTP credentials missing.")
        return False
    try:
        msg = MIMEText(body_text)
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        print(f"Email successfully sent to {to_email}")
        return True
    except Exception as e:
        print(f"SMTP execution failure: {str(e)}")
        return False

@app.get("/")
def dashboard():
    user_doc = db.collection("users").document("user_minh").get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    return render_template("index.html", **check_in_view_data(user_data.get("last_check_in")))

@app.post("/api/check-in")
def send_check_in():
    """Endpoint triggered when user clicks the '✓ I am safe' button."""
    try:
        user_ref = db.collection("users").document("user_minh")
        user_ref.update({
            "last_check_in": firestore.SERVER_TIMESTAMP,
            "status": "OK",
            "reminder_sent": False,
            "alert_sent": False
        })
        user_data = user_ref.get().to_dict() or {}
        view_data = check_in_view_data(user_data.get("last_check_in"))
        return jsonify(
            message="Safe-button pressed! Timers reset successfully.",
            **view_data,
        )
    except Exception as e:
        return jsonify(
            error="Could not register check-in. Database connection issue.",
            technical_details=str(e)
        ), 500

@app.post("/api/cron-check")
def background_timer_check():
    """Merged Cron Background Route triggered by Cloud Scheduler every 15 mins."""
    # Security Check: Ensure the request is coming from our Cloud Scheduler service account token
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return jsonify(error="Unauthorized invocation block."), 401

    try:
        user_ref = db.collection("users").document("user_minh")
        user_doc = user_ref.get()

        if not user_doc.exists:
            return jsonify(error="User document profile 'user_minh' not found."), 404

        user_data = user_doc.to_dict()
        last_check_in = user_data.get("last_check_in")
        
        if not last_check_in:
            return jsonify(error="No check-in timestamp found."), 400

        # Calculate time elapsed since last button press
        now = datetime.now(timezone.utc)
        elapsed_time = now - last_check_in
        elapsed_hours = elapsed_time.total_seconds() / 3600.0

        # Extract timing thresholds and contacts maps securely from database
        settings = user_data.get("settings", {})
        contacts = user_data.get("contacts", {})
        print(f"Settings retrieved: {settings}")
        print(f"Contacts retrieved: {contacts}")
        
        reminder_buffer = settings.get("reminder_buffer_hours", 24)
        alert_buffer = settings.get("alert_buffer_hours", 28)
        
        my_email = contacts.get("my_email")
        family_email = contacts.get("family_email")

        print(f"System status check. Hours elapsed: {elapsed_hours:.2f}")

        # Evaluation Matrix Logic
        # Scenario 3: Emergency Window Expired (Over 28 Hours)
        if elapsed_hours > alert_buffer and not user_data.get("alert_sent", False):
            subject = "URGENT: Safe-Button Alert Activated"
            body = f"Emergency Alert. Minh has missed their safety check-in window. Last contact: {last_check_in}."
            if send_alert_email(family_email, subject, body):
                user_ref.update({"status": "TRIGGERED", "alert_sent": True})

        # Scenario 2: Late / Warning Window (Between 24 and 28 Hours)
        elif elapsed_hours > reminder_buffer and not user_data.get("reminder_sent", False):
            subject = "Safe-Button Reminder Notification"
            body = "You missed your 24-hour check-in window. Please open https://okcfisher.org to reset your timers."
            if send_alert_email(my_email, subject, body):
                user_ref.update({"reminder_sent": True})

        # Scenario 1: Safe Default State (Under 24 Hours)
        else:
            print("Status: User check-in is valid. No notifications required.")

        return jsonify(status="Heartbeat calculated successfully.", hours_elapsed=round(elapsed_hours, 2))

    except Exception as e:
        return jsonify(error="Internal calculation processing failure.", details=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
