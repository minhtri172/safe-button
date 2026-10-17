#!/bin/bash
# EC2 user data (Amazon Linux 2023). Logs: /var/log/user-data.log
exec > >(tee /var/log/user-data.log) 2>&1
set -euxo pipefail

# ---- Edit these ----
REPO_URL="https://github.com/minhtri172/safe-button.git"
BRANCH="aws-project2"
AWS_REGION="us-east-1"
CLUSTER_NAME="safe-button"
TF_VERSION="1.9.8"
RUN_AS="ec2-user"
HOME_DIR="/home/${RUN_AS}"
# --------------------

dnf install -y git unzip jq

# Terraform
curl -fsSLo /tmp/tf.zip "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip"
unzip -o /tmp/tf.zip -d /usr/local/bin terraform

# kubectl
curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x /usr/local/bin/kubectl

# AWS CLI v2 is preinstalled on Amazon Linux 2023

# Run the deployment as ec2-user (instance role supplies AWS credentials)
sudo -u "$RUN_AS" -H bash -euxo pipefail <<EOF
cd "${HOME_DIR}"
git clone --branch "${BRANCH}" "${REPO_URL}" safe-button
cd safe-button/terraform
cat > terraform.tfvars <<'TFVARS'
aws_region         = "us-east-1"
cluster_name       = "safe-button"
kubernetes_version = "1.36"

cluster_role_arn      = "arn:aws:iam::119997536442:role/AmazonEKSAutoClusterRole"
node_role_arn         = "arn:aws:iam::119997536442:role/AmazonEKSAutoNodeRole"
pod_identity_role_arn = "arn:aws:iam::119997536442:role/PodRole"

vpc_id = "vpc-0f864ee8137743e08"

subnet_ids = [
  "subnet-0e471cecc5c6b9006",
  "subnet-02168fa63ff94dc7d",
  "subnet-00ef86e85039d88be",
  "subnet-0a43265d0635ecd42",
  "subnet-078ab2f505fd3a409",
]

admin_principal_arns = [
  "arn:aws:iam::119997536442:user/minhle",
  "arn:aws:iam::119997536442:role/safe-button-deploy-role",
  "arn:aws:iam::119997536442:role/safe-button-terraform-runner",
]
TFVARS
# Remove leftovers from a previous cluster (fresh instance has empty local state)
aws kms delete-alias --region "${AWS_REGION}" --alias-name "alias/eks/${CLUSTER_NAME}" || true
aws logs delete-log-group --region "${AWS_REGION}" --log-group-name "/aws/eks/${CLUSTER_NAME}/cluster" || true
terraform init
terraform apply -auto-approve -var-file=terraform.tfvars

cd ..
aws eks update-kubeconfig --region "${AWS_REGION}" --name "${CLUSTER_NAME}"
kubectl apply -f service-account.yaml
kubectl apply -f rendered-k8s-deployment.yaml
EOF

# Point Route 53 at the NLB created by the Service
DOMAIN="okcfisher.click."
ZONE_ID="Z07058142VJEPDC8Q1CY0"
sudo -u "$RUN_AS" -H env AWS_REGION="$AWS_REGION" DOMAIN="$DOMAIN" ZONE_ID="$ZONE_ID" bash -euxo pipefail <<'EOF'
for i in $(seq 1 60); do
  LB_HOST=$(kubectl get svc safe-button -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' || true)
  [ -n "$LB_HOST" ] && break
  sleep 10
done
[ -n "$LB_HOST" ]
read -r LB_ZONE LB_DNS < <(aws elbv2 describe-load-balancers --region "$AWS_REGION" \
  --query "LoadBalancers[?DNSName=='$LB_HOST']|[0].[CanonicalHostedZoneId,DNSName]" --output text)
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch "{
  \"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{
    \"Name\":\"$DOMAIN\",\"Type\":\"A\",
    \"AliasTarget\":{\"HostedZoneId\":\"$LB_ZONE\",\"DNSName\":\"$LB_DNS\",\"EvaluateTargetHealth\":false}}}]}"
EOF

# Destroy Terraform resources when the instance is stopped/terminated
cat > /etc/systemd/system/tf-destroy.service <<UNIT
[Unit]
Description=Terraform destroy on shutdown
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/true
ExecStop=-/bin/bash -c 'sudo -u ${RUN_AS} -H kubectl delete svc safe-button --timeout=10m'
ExecStop=/bin/bash -c 'cd ${HOME_DIR}/safe-button/terraform && sudo -u ${RUN_AS} -H /usr/local/bin/terraform destroy -auto-approve -var-file=terraform.tfvars'
TimeoutStopSec=1800

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now tf-destroy.service
