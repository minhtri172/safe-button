# Safe Button EKS Terraform

This configuration manages the EKS Auto Mode cluster, its API-based admin access entries and policy associations, and the app's EKS Pod Identity association. It uses the existing VPC subnets and IAM roles from the project; it does not create or modify those shared resources.

The Kubernetes Deployment and LoadBalancer Service remain managed by the existing CodePipeline deployment. Route 53 record changes, the EventBridge cron rule's enabled state, and Kubernetes Secrets are also outside this Terraform state. The cron token is deliberately not read by Terraform, so it is not stored in Terraform state.

## Prerequisites

- Terraform 1.5 or later and AWS provider 5.79 or later (below 7.0).
- AWS credentials with permissions to manage EKS clusters, access entries and policy associations, and Pod Identity associations.
- Existing cluster and node IAM roles, a Pod Identity role, and at least two existing subnets in the target VPC.
- The Pod Identity role trust policy must trust `pods.eks.amazonaws.com` for `sts:AssumeRole` and `sts:TagSession`. Its permissions should be limited to the app's required resources.
- The node role and cluster role must have the AWS-documented permissions and trust relationships for EKS Auto Mode.

## Configure and validate

From this directory, copy `terraform.tfvars.example` to `terraform.tfvars` and review every account-, subnet-, role-, and principal-specific value.

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check
terraform validate
terraform plan
```

Review the plan carefully before applying. The cluster is configured with both public and private Kubernetes API endpoints, API-only authentication, Auto Mode node pools (`general-purpose` and `system`), load balancing, block storage, and all five EKS control-plane log types.

## Existing cluster/state caution

If the cluster or any of the access entries, access-policy associations, or Pod Identity association already exist, import them into this Terraform state before applying. A fresh local state file does not mean the AWS resources are new. Do not apply a plan that proposes to create duplicate resources or replace an existing cluster unexpectedly.

This directory does not configure a remote backend. For team or production use, configure a secured, shared backend with encryption, access controls, and state locking before the first `terraform init`/apply. Terraform state can contain sensitive infrastructure data; do not commit it.

## Lifecycle boundary

After importing/creating these resources, use Terraform for the EKS cluster and its access associations. Do not use `eks-teardown-restore.ps1` to delete or recreate the cluster; Terraform will not know about changes made by that script.

The app deployment stays in the pipeline. Before destroying the cluster, disable the EventBridge cron rule and remove the Kubernetes Service while the cluster is still available, then remove the Route 53 alias record. This lets the Kubernetes load-balancer controller clean up the NLB and avoids leaving DNS pointed at it. Then review and run `terraform destroy`. Recreate the cluster with `terraform apply`, deploy the app through the pipeline, update the DNS alias, and re-enable the cron rule.
