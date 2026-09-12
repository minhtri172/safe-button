# Daily Check-In Dashboard

A small Flask app for a daily safety check-in dashboard.

## Run locally

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py app.py
```

Open http://127.0.0.1:5000 in a browser.

## Email setup

Set these environment variables before starting Flask. Use an app password for providers that require one.

```powershell
$env:SMTP_HOST = "smtp.example.com"
$env:SMTP_PORT = "587"
$env:SMTP_USERNAME = "your-account@example.com"
$env:SMTP_PASSWORD = "your-app-password"
$env:SMTP_FROM = "your-account@example.com"
$env:SAFE_EMAIL_TO = "trusted-contact@example.com"
py app.py
```

The `I am safe` button sends a timestamped email to `SAFE_EMAIL_TO` through the configured SMTP server.
