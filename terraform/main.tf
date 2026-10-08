module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  # Optional
  endpoint_public_access = true

  # The caller is already listed in var.admin_principal_arns; enabling this would create a duplicate access entry.
  enable_cluster_creator_admin_permissions = false

  # Create just the IAM resources for EKS Auto Mode for use with custom node pools
  create_auto_mode_iam_resources = true
  compute_config = {
    enabled    = true
    node_pools = ["general-purpose", "system"]
  }

  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids

  access_entries = {
    for idx, arn in var.admin_principal_arns : "admin_${idx}" => {
      principal_arn = arn

      policy_associations = {
        admin = {
          policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = {
            type = "cluster"
          }
        }
      }
    }
  }

  tags = {
    Environment = "dev"
    Terraform   = "true"
  }
}

# 1. Fetch the SSM parameter value
data "aws_ssm_parameter" "cron_auth_token" {
  name            = "/safe-button/cron-auth-token"
  with_decryption = true
}

# 2. Use the token inside your Kubernetes Secret resource
resource "kubernetes_secret_v1" "safe-button-cron" {
  metadata {
    name      = "safe-button-cron"
    namespace = "default"
  }

  data = {
    CRON_AUTH_TOKEN = data.aws_ssm_parameter.cron_auth_token.value
  }

  type = "Opaque"
}

# 1. Generate a 32-byte (64 hex characters) random string equivalent to `openssl rand -hex 32`
resource "random_id" "flask_secret_key" {
  byte_length = 32
}

# 2. Create the Kubernetes Secret (Terraform manages state, so it automatically skips if already present)
resource "kubernetes_secret_v1" "safe_button_flask" {
  metadata {
    name      = "safe-button-flask"
    namespace = "default" # Adjust namespace if needed
  }

  data = {
    FLASK_SECRET_KEY = random_id.flask_secret_key.hex
  }

  type = "Opaque"
}

resource "aws_eks_pod_identity_association" "app" {
  cluster_name    = module.eks.cluster_name
  namespace       = var.pod_identity_namespace
  service_account = var.pod_identity_service_account
  role_arn        = var.pod_identity_role_arn
}
