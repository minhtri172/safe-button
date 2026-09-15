import os
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, render_template
from google.cloud import firestore


def format_current_date():
    return datetime.now().strftime("%A, %B %-d")


def format_last_check_in(value):
    if value is None:
        return "Never"

    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    label_day = "Today" if value.date() == now.date() else value.strftime("%A")
    return f"{label_day} at {value.strftime('%I:%M %p').lstrip('0')}"


app = Flask(__name__, static_folder="css", static_url_path="/css")

# Initialize Firestore Client
# It automatically picks up your Google Cloud Project credentials in production
db = firestore.Client()

CHECK_IN_WINDOW = timedelta(hours=24)


def get_check_in_deadline():
    user_data = db.collection("users").document("user_minh").get().to_dict() or {}
    last_check_in = user_data.get("last_check_in")

    if last_check_in is None:
        last_check_in = datetime.now(timezone.utc)
    elif last_check_in.tzinfo is None:
        last_check_in = last_check_in.replace(tzinfo=timezone.utc)

    return last_check_in + CHECK_IN_WINDOW

@app.get("/")
def dashboard():
    try:
        deadline = get_check_in_deadline()
        last_check_in = db.collection("users").document("user_minh").get().to_dict().get("last_check_in")
    except Exception:
        deadline = datetime.now(timezone.utc) + CHECK_IN_WINDOW
        last_check_in = None

    return render_template(
        "index.html",
        check_in_deadline=deadline.isoformat(),
        current_date=format_current_date(),
        last_check_in_label=format_last_check_in(last_check_in),
    )

@app.post("/api/check-in")
def send_check_in():
    try:
        # 1. Target your specific user profile document in Firestore
        # Replace 'your_user_id' with your actual custom document ID later
        user_ref = db.collection("users").document("user_minh")
        
        # 2. Reset everything to safe default states instantly
        check_in_time = datetime.now(timezone.utc)
        user_ref.update({
            "last_check_in": check_in_time,
            "status": "OK",
            "reminder_sent": False,
            "alert_sent": False
        })
        
        return jsonify(
            message="Safe-button pressed! Timers reset successfully.",
            check_in_deadline=(check_in_time + CHECK_IN_WINDOW).isoformat(),
            last_check_in_label=format_last_check_in(check_in_time),
        )
        
    except Exception as e:
        # If the database connection fails, return the error details
        return jsonify(
            error="Could not register check-in. Database connection issue.",
            technical_details=str(e)
        ), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
