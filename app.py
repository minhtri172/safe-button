import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from dotenv import load_dotenv

from flask import Flask, jsonify, render_template

load_dotenv()

app = Flask(__name__, static_folder="css", static_url_path="/css")


@app.get("/")
def dashboard():
    return render_template("index.html")


@app.post("/api/check-in")
def send_check_in():
    required_settings = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM", "SAFE_EMAIL_TO")
    if any(not os.getenv(setting) for setting in required_settings):
        return jsonify(error="Email delivery is not configured yet."), 503

    message = EmailMessage()
    message["Subject"] = "Daily check-in: I am safe"
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = os.environ["SAFE_EMAIL_TO"]
    message.set_content(
        "This is a daily safety check-in. I am safe and checked in at "
        f"{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}."
    )

    try:
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(os.environ["SMTP_HOST"], smtp_port, timeout=10) as smtp:
            smtp.set_debuglevel(1)
            smtp.starttls()
            smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException):
        return jsonify(error="The safe message could not be sent. Please try again."), 502

    return jsonify(message="Safe message sent.")


if __name__ == "__main__":
    app.run(debug=True)
