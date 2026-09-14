import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from google.cloud import firestore

# Initialize the Firestore Client
db = firestore.Client()

def check_safe_button(event, context):
    """
    Triggered by a Cloud Scheduler event via Pub/Sub.
    Checks if the user has missed their safe-button check-in window.
    """
    # 1. Fetch your user record from the 'users' collection
    # Replace 'your_user_id' with your actual custom document ID later
    user_ref = db.collection("users").document("user_minh")
    user_data = user_ref.get().to_dict()
    
    if not user_data:
        print("Error: No user configuration data found in Firestore.")
        return "No user found"

    # 2. Get the last check-in time and compute elapsed hours
    last_check_in = user_data["last_check_in"]
    now = datetime.now(timezone.utc)
    elapsed_hours = (now - last_check_in).total_seconds() / 3600
    
    print(f"System status check. Hours elapsed since last button press: {elapsed_hours:.2f}")

    # Read tracking flags and buffer configurations
    reminder_sent = user_data.get("reminder_sent", False)
    alert_sent = user_data.get("alert_sent", False)
    reminder_buffer = user_data.get("settings", {}).get("reminder_buffer_hours", 24)
    alert_buffer = user_data.get("settings", {}).get("alert_buffer_hours", 28)

    # 3. Scenario A: Missed primary window -> Send Reminder to YOU
    if elapsed_hours > reminder_buffer and not reminder_sent:
        try:
            send_email(
                to=user_data["contacts"]["my_email"],
                subject="Reminder: Please press your Safe-Button",
                body="Hello! You missed your standard check-in window. Please visit the app and press the button to reset your timers."
            )
            user_ref.update({"reminder_sent": True})
            print("Action: Reminder email successfully sent to user.")
        except Exception as e:
            print(f"Failed to deliver reminder email: {e}")

    # 4. Scenario B: Missed grace period -> Send Emergency Alert to FAMILY
    elif elapsed_hours > alert_buffer and not alert_sent:
        try:
            send_email(
                to=user_data["contacts"]["family_email"],
                subject="ALERT: Safety Check-in Missed",
                body=f"This is an automated alert. Your family member has missed their safe-button window and has not checked in for over {alert_buffer} hours."
            )
            user_ref.update({"alert_sent": True, "status": "TRIGGERED"})
            print("Action: Critical alert email successfully distributed to family contacts.")
        except Exception as e:
            print(f"Failed to deliver emergency alert email: {e}")
            
    else:
        print("Status: User check-in is valid. No notifications required.")

def send_email(to, subject, body):
    """Helper function to build and dispatch SMTP email payloads."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = to
    message.set_content(body)

    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USERNAME"]
    smtp_pass = os.environ["SMTP_PASSWORD"]

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_pass)
        smtp.send_message(message)
