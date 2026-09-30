# Daily Check-In Dashboard

A small Flask app for a daily safety check-in dashboard.

## Run locally

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py main.py
```

Set AWS credentials using the standard AWS SDK credential chain and create a DynamoDB table named `safe-button-users` with a string partition key named `id`. Set `DYNAMODB_TABLE` and `AWS_REGION` to override the defaults. Each signed-in user's Cognito `sub` is used as the record's `id`.

Open http://127.0.0.1:8080 in a browser.

## Cognito sign-in

In the Cognito user pool, configure an app client for the authorization code grant with the `openid` and `email` scopes. Add these allowed callback and sign-out URLs to the app client:

- Local callback: `http://localhost:8080/auth/callback`
- Local sign-out: `http://localhost:8080/`
- Production callback: `https://YOUR_DOMAIN/auth/callback`
- Production sign-out: `https://YOUR_DOMAIN/`

Configure a Cognito domain for the user pool. Set these application environment variables to match the pool and app client:

```text
COGNITO_REGION=us-east-1
COGNITO_USER_POOL_ID=your-user-pool-id
COGNITO_CLIENT_ID=your-app-client-id
COGNITO_CALLBACK_URL=https://YOUR_DOMAIN/auth/callback
COGNITO_DOMAIN=https://your-cognito-domain.auth.us-east-1.amazoncognito.com
COGNITO_LOGOUT_URI=https://YOUR_DOMAIN/
FLASK_SECRET_KEY=long-random-stable-secret
SESSION_COOKIE_SECURE=true
```

For local development, use `http://localhost:8080/auth/callback` for `COGNITO_CALLBACK_URL` and leave `SESSION_COOKIE_SECURE` set to `false`. Store the Cognito app-client secret as the `COGNITO_CLIENT_SECRET` environment variable or Systems Manager Parameter Store parameter. Do not commit either the client secret or Flask secret key. The app stores each signed-in user's record using the Cognito `sub` as the DynamoDB `id`.

## AWS deployment

The application uses DynamoDB. Create a table named `safe-button-users` with a string partition key named `id`, or set `DYNAMODB_TABLE` to another table name. Grant the App Runner instance role `dynamodb:GetItem`, `dynamodb:UpdateItem`, and `dynamodb:Scan` on that table. Use the standard AWS credential chain locally and IAM roles in AWS; do not put access keys in the container or deployment files.

For the web service, create an ECR repository and an App Runner service configured to use its `latest` image tag with automatic deployments enabled. Configure the App Runner ECR access role and instance role. Create a CodeBuild project using `buildspec.yml`, a standard Linux image with Docker privileged mode enabled, and the ECR repository name in the `ECR_REPOSITORY_NAME` environment variable. Allow its service role to push to that repository. Each successful build pushes `latest`, which triggers the App Runner deployment.

For the 15-minute check, create a Secrets Manager secret whose `SecretString` is a random cron bearer token. Configure the App Runner service environment variable `CRON_AUTH_TOKEN` from that secret. Deploy `template.yaml` with AWS SAM, supplying `CronCheckUrl` as the full App Runner `/api/cron-check` URL and `CronAuthSecretArn` as the secret ARN. The template creates an EventBridge Scheduler schedule and a Lambda invoker; its role can read only the configured secret. Set `ScheduleExpression` to change the default `rate(15 minutes)` interval.

The Kubernetes manifest is an alternative deployment path. On EKS, use EKS Pod Identity or IRSA to grant the pod DynamoDB access rather than static credentials.

## Email setup

Configure Amazon SES in the same AWS region as the app, verify the sender identity, and create SES SMTP credentials. If the SES account is still in the sandbox, verify recipient identities too. Set these environment variables before starting Flask:

```powershell
$env:SMTP_HOST = "email-smtp.us-east-1.amazonaws.com"
$env:SMTP_PORT = "587"
$env:SMTP_USERNAME = "your-account@example.com"
$env:SMTP_PASSWORD = "your-app-password"
$env:SMTP_FROM = "your-account@example.com"
$env:SAFE_EMAIL_TO = "trusted-contact@example.com"
py app.py
```

The `I am safe` button updates the check-in record in DynamoDB. Configure SMTP credentials to enable reminder and emergency emails.
