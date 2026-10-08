variable "aws_region" {
  description = "AWS region for the EKS cluster."
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "Name of the EKS cluster."
  type        = string
  default     = "safe-button"
}

variable "kubernetes_version" {
  description = "Kubernetes version for the EKS control plane."
  type        = string
  default     = "1.36"

  validation {
    condition     = can(regex("^\\d+\\.\\d+$", var.kubernetes_version))
    error_message = "kubernetes_version must have the form MAJOR.MINOR, for example 1.36."
  }
}

variable "cluster_role_arn" {
  description = "ARN of the existing IAM role used by the EKS control plane."
  type        = string
}

variable "node_role_arn" {
  description = "ARN of the existing IAM role used by EKS Auto Mode nodes."
  type        = string
}

variable "vpc_id" {
  description = "Existing VPC ID for the EKS cluster."
  type        = string
}

variable "subnet_ids" {
  description = "Existing VPC subnet IDs for the EKS cluster."
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2 && length(distinct(var.subnet_ids)) == length(var.subnet_ids)
    error_message = "Provide at least two unique subnet IDs."
  }
}

variable "admin_principal_arns" {
  description = "IAM user or role ARNs to grant EKS cluster-admin access."
  type        = set(string)

  validation {
    condition     = length(var.admin_principal_arns) > 0
    error_message = "Provide at least one admin principal ARN."
  }
}

variable "pod_identity_role_arn" {
  description = "ARN of the existing IAM role associated with the app's Kubernetes service account."
  type        = string
}

variable "pod_identity_namespace" {
  description = "Kubernetes namespace for the app's Pod Identity association."
  type        = string
  default     = "default"
}

variable "pod_identity_service_account" {
  description = "Kubernetes service account for the app's Pod Identity association."
  type        = string
  default     = "my-pod-service-account"
}

variable "tags" {
  description = "Tags applied to resources managed by this Terraform configuration."
  type        = map(string)
  default = {
    Project   = "safe-button"
    ManagedBy = "Terraform"
  }
}
