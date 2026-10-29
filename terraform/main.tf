module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  # Optional
  endpoint_public_access = true

  # EC2 runner in the same VPC resolves the API endpoint to the private ENI; allow it in.
  security_group_additional_rules = {
    vpc_api_access = {
      description = "Kubernetes API from the VPC"
      protocol    = "tcp"
      from_port   = 443
      to_port     = 443
      type        = "ingress"
      cidr_blocks = ["172.31.0.0/16"]
    }
  }

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
  name            = "auth-token"
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

  depends_on = [module.eks]
}

resource "aws_eks_pod_identity_association" "app" {
  cluster_name    = module.eks.cluster_name
  namespace       = var.pod_identity_namespace
  service_account = var.pod_identity_service_account
  role_arn        = var.pod_identity_role_arn
}
