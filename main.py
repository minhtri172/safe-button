import os
import smtplib
import hmac
import re
from email.mime.text import MIMEText
from datetime import datetime, timezone
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
import boto3
from authlib.integrations.flask_client import OAuth
from urllib.parse import urlencode


app = Flask(__name__, static_folder="css", static_url_path="/css")

app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
oauth = OAuth(app)

# DynamoDB table uses `id` as its partition key.
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "SafeButtonTable")
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(DYNAMODB_TABLE_NAME)
CRON_AUTH_TOKEN = os.environ.get("CRON_AUTH_TOKEN", "")



def get_smtp_config():
    ssm = boto3.client('ssm', region_name='us-east-1')
    
    # Request all three configurations by their names in a list
    response = ssm.get_parameters(
        Names=['smtp-from', 'smtp-password', 'smtp-username', 'COGNITO_CLIENT_SECRET'],
        WithDecryption=True  # Automatically decrypts the smtp-password SecureString
    )
    
    # Map the results into a readable dictionary
    config = {param['Name']: param['Value'] for param in response['Parameters']}
    return config

# Usage
smtp_settings = get_smtp_config()

# Core SMTP Email Configurations (Reads from Environment Variables)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = smtp_settings.get('smtp-username')
SMTP_PASSWORD = smtp_settings.get('smtp-password')
SMTP_FROM = smtp_settings.get('smtp-from')
CHECK_IN_WINDOW_HOURS = 24
COGNITO_CLIENT_SECRET = os.environ.get("COGNITO_CLIENT_SECRET") or smtp_settings.get("COGNITO_CLIENT_SECRET")
COGNITO_REGION = os.environ.get("COGNITO_REGION", "us-east-1")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "us-east-1_TLvRsA5Hf")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "138p7dqp9s0bu3eofc78oom7op")
COGNITO_ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"

oauth.register(
    name="oidc",
    client_id=COGNITO_CLIENT_ID,
    client_secret=COGNITO_CLIENT_SECRET,
    server_metadata_url=f"{COGNITO_ISSUER}/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email"},
)

@app.get("/login")
def login():
    callback_url = os.environ.get("COGNITO_CALLBACK_URL") or url_for("authorize", _external=True)
    return oauth.oidc.authorize_redirect(callback_url)

@app.get("/auth/callback")
def authorize():
    token = oauth.oidc.authorize_access_token()
    user_info = token.get("userinfo", {})
    if not user_info.get("sub"):
        return "Cognito did not return a user identity.", 401
    session.clear()
    session["user"] = {
        "sub": user_info["sub"],
        "email": user_info.get("email", ""),
    }
    return redirect(url_for("manage_contacts"))

@app.get("/logout")
def logout():
    session.clear()
    cognito_domain = os.environ.get("COGNITO_DOMAIN", "").rstrip("/")
    if cognito_domain:
        logout_uri = os.environ.get("COGNITO_LOGOUT_URI") or url_for("dashboard", _external=True)
        query = urlencode({"client_id": COGNITO_CLIENT_ID, "logout_uri": logout_uri})
        return redirect(f"{cognito_domain}/logout?{query}")
    return redirect(url_for("dashboard"))

def check_in_view_data(last_check_in=None):
    """Build the timestamps and labels used by the dashboard."""
    now = datetime.now(timezone.utc)
    if isinstance(last_check_in, str):
        last_check_in = datetime.fromisoformat(last_check_in.replace("Z", "+00:00"))
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
    user = session.get("user")
    if not user:
        return render_template("login.html")
    response = table.get_item(Key={"id": user["sub"]})
    user_data = response.get("Item", {})
    return render_template(
        "index.html",
        user_email=user.get("email", ""),
        **check_in_view_data(user_data.get("last_check_in")),
    )

@app.route("/contacts", methods=["GET", "POST"])
def manage_contacts():
    user = session.get("user")
    if not user:
        return redirect(url_for("login"))
    response = table.get_item(Key={"id": user["sub"]})
    user_data = response.get("Item", {})
    contacts = user_data.get("contacts", {})
    error = None

    if request.method == "POST":
        my_email = request.form.get("my_email", "").strip()
        family_email = request.form.get("family_email", "").strip()
        email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

        if not all(
            len(address) <= 254 and email_pattern.fullmatch(address)
            for address in (my_email, family_email)
        ):
            error = "Enter a valid email address in both fields."
        else:
            contacts = {"my_email": my_email, "family_email": family_email}
            table.update_item(
                Key={"id": user["sub"]},
                UpdateExpression="SET contacts = :contacts",
                ExpressionAttributeValues={":contacts": contacts},
            )
            return redirect(url_for("dashboard"))

    return render_template(
        "contacts.html",
        my_email=contacts.get("my_email", ""),
        family_email=contacts.get("family_email", ""),
        error=error,
        saved=False,
    ), 400 if error else 200

@app.post("/api/check-in")
def send_check_in():
    """Endpoint triggered when user clicks the '✓ I am safe' button."""
    user = session.get("user")
    if not user:
        return jsonify(error="Sign in to check in."), 401
    try:
        checked_in_at = datetime.now(timezone.utc).isoformat()
        table.update_item(
            Key={"id": user["sub"]},
            UpdateExpression="SET alert_sent = :alert_sent, last_check_in = :last_check_in, reminder_sent = :reminder_sent, settings = :settings, #status = :status",
            ExpressionAttributeNames={
                "#status": "status"
            },
            ExpressionAttributeValues={
                ":alert_sent": False,
                ":last_check_in": checked_in_at,
                ":reminder_sent": False,
                ":settings": {
                    "alert_buffer_hours": 28,
                    "reminder_buffer_hours": 24
                },
                ":status": "OK"
            }
        )

        view_data = check_in_view_data(checked_in_at)
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
    """Endpoint triggered by cron job to evaluate user check-in status."""
    authorization = request.headers.get("Authorization", "")
    expected_authorization = f"Bearer {CRON_AUTH_TOKEN}"
    if not CRON_AUTH_TOKEN or not hmac.compare_digest(authorization, expected_authorization):
        return jsonify(error="Unauthorized invocation block."), 401

    try:
        scan_kwargs = {}
        checked_users = 0
        while True:
            response = table.scan(**scan_kwargs)
            for user_data in response.get("Items", []):
                user_id = user_data.get("id")
                last_check_in = user_data.get("last_check_in")
                if not user_id or not last_check_in:
                    continue
                if isinstance(last_check_in, str):
                    last_check_in = datetime.fromisoformat(last_check_in.replace("Z", "+00:00"))
                if last_check_in.tzinfo is None:
                    last_check_in = last_check_in.replace(tzinfo=timezone.utc)

                elapsed_hours = (datetime.now(timezone.utc) - last_check_in).total_seconds() / 3600.0
                settings = user_data.get("settings", {})
                contacts = user_data.get("contacts", {})
                reminder_buffer = float(settings.get("reminder_buffer_hours", 24))
                alert_buffer = float(settings.get("alert_buffer_hours", 28))
                checked_users += 1

                if elapsed_hours > alert_buffer and not user_data.get("alert_sent", False):
                    family_email = contacts.get("family_email")
                    if family_email and send_alert_email(
                        family_email,
                        "URGENT: Safe-Button Alert Activated",
                        f"Emergency Alert. The user has missed their safety check-in window. Last contact: {last_check_in}.",
                    ):
                        table.update_item(
                            Key={"id": user_id},
                            UpdateExpression="SET #status = :status, alert_sent = :alert_sent",
                            ExpressionAttributeNames={"#status": "status"},
                            ExpressionAttributeValues={":status": "TRIGGERED", ":alert_sent": True},
                        )
                elif reminder_buffer < elapsed_hours <= alert_buffer and not user_data.get("reminder_sent", False):
                    my_email = contacts.get("my_email")
                    if my_email and send_alert_email(
                        my_email,
                        "Safe-Button Reminder Notification",
                        "You missed your 24-hour check-in window. Please open the Safe-Button app to reset your timer.",
                    ):
                        table.update_item(
                            Key={"id": user_id},
                            UpdateExpression="SET reminder_sent = :reminder_sent",
                            ExpressionAttributeValues={":reminder_sent": True},
                        )

            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

        return jsonify(status="Heartbeat calculated successfully.", users_checked=checked_users)

    except Exception as e:
        return jsonify(error="Internal calculation processing failure.", details=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
