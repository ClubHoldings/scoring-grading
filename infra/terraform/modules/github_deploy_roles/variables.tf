variable "project" {
  type        = string
  description = "Project slug; deploy role policies are scoped to resources named <project>-*."
}

variable "environment" {
  type        = string
  description = "Environment name (dev, prod)."
}

variable "account_id" {
  type        = string
  description = "AWS account ID."
}

variable "github_repo" {
  type        = string
  description = "owner/name of the repository allowed to assume the roles."
}

variable "apply_environment" {
  type        = string
  description = "GitHub Environment whose jobs may assume the apply role (prod has required reviewers)."
}

variable "plan_subjects" {
  type        = list(string)
  description = "OIDC subject patterns allowed to assume the read-only plan role."
}

variable "state_bucket_arn" {
  type        = string
  description = "State bucket ARN; both roles need it for state and lock access."
}

variable "create_oidc_provider" {
  type        = bool
  default     = true
  description = "Set false if the account already has the GitHub OIDC provider (only one is allowed)."
}
