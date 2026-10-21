import os
import smtplib
import hmac
import re
from email.mime.text import MIMEText
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
#table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(DYNAMODB_TABLE_NAME)

dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)
CRON_AUTH_TOKEN = os.environ.get("CRON_AUTH_TOKEN", "")



def get_smtp_config():
    ssm = boto3.client('ssm', region_name='us-east-1')
    
    # Get values
    response = ssm.get_parameters(
        Names=['smtp-from', 'smtp-password', 'smtp-username', 'COGNITO_CLIENT_SECRET'],
        WithDecryption=True
    )
    
    # Map to a dict
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
COGNITO_CLIENT_SECRET = smtp_settings.get("COGNITO_CLIENT_SECRET")


oauth.register(
  name='oidc',
  authority='https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TLvRsA5Hf',
  client_id='138p7dqp9s0bu3eofc78oom7op',
  client_secret=COGNITO_CLIENT_SECRET,
  server_metadata_url='https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TLvRsA5Hf/.well-known/openid-configuration',
  client_kwargs={'scope': 'email openid phone'}
)

@app.get("/login")
def login():
    return oauth.oidc.authorize_redirect("https://okcfisher.click/auth/callback")

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
    return redirect(url_for("dashboard"))

def check_in_view_data(last_check_in=None):
    
    now = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    
    if last_check_in is None:
        last_check_in = now
    else:
        last_check_in = datetime.fromisoformat(last_check_in)
  
    window_start = last_check_in or now
    deadline = window_start.timestamp() + (CHECK_IN_WINDOW_HOURS * 60 * 60)
    deadline = datetime.fromtimestamp(deadline, timezone.utc)
    deadline = deadline.astimezone(ZoneInfo("America/New_York"))

    return {
        "current_date": now.strftime("%A, %B %d, %Y"),
        "last_check_in_label": last_check_in.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S"),
        "check_in_deadline": deadline.isoformat(),
    }

def send_alert_email(to_email, subject, body_text):
    
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
                ExpressionAttributeValues={":contacts": contacts}
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
    user = session.get("user")
    if not user:
        return jsonify(error="Sign in to check in."), 401
    try:
        # Get check-in timestamp
        checked_in_at = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).isoformat()
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        
        # update the user's check-in record in the database
        my_email = table.get_item(Key={"id": user["sub"]})["Item"]["contacts"]["my_email"]
        family_email = table.get_item(Key={"id": user["sub"]})["Item"]["contacts"]["family_email"]
        table.put_item(
            Item={
                "id": user["sub"],
                "alert_sent": False,
                "last_check_in": checked_in_at,
                "reminder_sent": False,
                "contacts": {
                    "my_email": my_email,
                    "family_email": family_email
                },
                "settings": {
                    "alert_buffer_hours": 28,
                    "reminder_buffer_hours": 24
                },
                "status": "OK"
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
    authorization = request.headers.get("Authorization", "")
    expected_authorization = f"Bearer {CRON_AUTH_TOKEN}"
    if not CRON_AUTH_TOKEN or not hmac.compare_digest(authorization, expected_authorization):
        return jsonify(error="Unauthorized invocation block."), 401
    

    try:
        users = table.scan()['Items']
        for user in users:
            last_check_in = user.get("last_check_in")
            last_check_in = datetime.fromisoformat(last_check_in)
            elapsed_hours = (datetime.now(timezone.utc) - last_check_in).total_seconds() / 3600.0
            reminder_buffer = user["settings"]["reminder_buffer_hours"]
            alert_buffer = user["settings"]["alert_buffer_hours"]
            print(f"Elapsed hours for user {user.get('id')}: {elapsed_hours}")
            
            if elapsed_hours > alert_buffer:
                family_email = user["contacts"]["family_email"]
                print(f"family_email: {family_email}")
                if family_email:
                    
                    send_alert_email(
                        family_email,
                        "URGENT: Safe-Button Alert Activated",
                        f"Emergency Alert. The user has missed their safety check-in window. Last contact: {last_check_in}.",
                    )

                    table.update_item(
                        Key={"id": user.get("id")},
                        UpdateExpression="SET #status = :status, alert_sent = :alert_sent",
                        ExpressionAttributeNames={
                            "#status": "status"
                        },
                        ExpressionAttributeValues={
                            ":status": "TRIGGERED",
                            ":alert_sent": True
                        }
                    )
                    
            elif reminder_buffer < elapsed_hours <= alert_buffer and not user.get("reminder_sent", False):
                my_email = user["contacts"]["my_email"]
                print(f"my_email: {my_email}")
                if my_email:
                    
                    send_alert_email(
                        my_email,
                        "Reminder: Safe-Button Check-In",
                        f"Reminder: The user has not checked in within the expected time window. Last contact: {last_check_in}.",
                    )
                    
                    table.update_item(
                        Key={"id": user.get("id")},
                        UpdateExpression="SET #status = :status, reminder_sent = :reminder_sent",
                        ExpressionAttributeNames={
                            "#status": "status"
                        },
                        ExpressionAttributeValues={
                            ":status": "REMINDER_SENT",
                            ":reminder_sent": True
                        }
                    )
        return jsonify(status="Heartbeat calculated successfully.")

    except Exception as e:
        return jsonify(error="Internal calculation processing failure.", details=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
