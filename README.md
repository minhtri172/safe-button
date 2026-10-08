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

## EKS deployment with CodePipeline

The EKS Auto Mode cluster and its access configuration can be managed with the Terraform project in [`terraform/`](./terraform/README.md). It reuses the existing IAM roles and VPC subnets; the CodePipeline continues to deploy the Kubernetes workload.

Use three pipeline stages after Source: Test, Build, then Deploy. The Test CodeBuild action uses `buildspec-test.yml` to install Python dependencies and run `pytest -q`; it needs no AWS access to application secrets. The Build CodeBuild action uses `buildspec.yml` to build and push a commit-tagged image, render that image into `rendered-k8s-deployment.yaml`, and publish the rendered manifest as its output artifact. Configure Docker privileged mode, `AWS_DEFAULT_REGION`, and `ECR_REPOSITORY_NAME` on the Build project. Its role needs ECR login and image-push permissions for the repository plus CloudWatch Logs permissions.

For a CodeBuild-based Deploy action, use `buildspec-deploy.yml` and pass it the Build artifact. Set `EKS_CLUSTER_NAME`, `AWS_DEFAULT_REGION`, and optionally `K8S_NAMESPACE` (defaults to `default`). The deploy role needs `eks:DescribeCluster` and an EKS access entry mapped to the Kubernetes group with permissions to update the workload. If you keep the native EKS `kubectl` action instead, it does not run a buildspec: give it the Build artifact and set its manifest path to `rendered-k8s-deployment.yaml`.

The ACM certificate ARN is currently configured in `k8s-deployment.yaml`; it must be in the same AWS region as the Network Load Balancer. The `safe-button` namespace must exist if you set `K8S_NAMESPACE=safe-button`.

Create the ECR repository, DynamoDB table, and `safe-button` namespace before running the pipeline. Associate an IAM role with the `safe-button` Kubernetes service account using EKS Pod Identity or IRSA; grant that role only the DynamoDB `GetItem`, `UpdateItem`, and `Scan` actions on the application table, plus `ssm:GetParameters` for the app's required parameter ARNs. At startup the app reads `smtp-from`, `smtp-password`, `smtp-username`, and `COGNITO_CLIENT_SECRET` from SSM Parameter Store in `us-east-1`. Set Cognito configuration, `CRON_AUTH_TOKEN`, and a stable `FLASK_SECRET_KEY` through environment variables or injected Kubernetes secrets; set `DYNAMODB_TABLE`/`AWS_REGION` as needed. Never put AWS credentials in the image or manifests. The Docker build context excludes the local `aws/` directory.

The service creates an internet-facing Network Load Balancer with HTTP on port 80 and TLS on port 443. The NLB terminates HTTPS using `ACM_CERTIFICATE_ARN` and forwards traffic to the app over HTTP on port 8080. Port 80 remains available without a redirect; configure an Ingress or another redirect layer if HTTP-to-HTTPS redirection is required.

AWS CodeDeploy does not provide a native EKS/Kubernetes deployment action. The supported pipeline here uses CodeBuild with `kubectl` for the EKS deploy stage; adding a CodeDeploy stage would require an EC2-based deployment host and would not make CodeDeploy manage the Kubernetes rollout. For CodeDeploy-managed blue/green deployments, use an ECS target instead.

For periodic check-ins, configure an external scheduler to call `/api/cron-check` with the configured bearer token. Do not use the removed App Runner/SAM setup for this EKS deployment path.

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
