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

variable "github_subject_prefix" {
  type        = string
  description = "OIDC subject prefix of the repository, e.g. repo:Owner@123/name@456. GitHub embeds numeric owner and repo IDs when the immutable-subject setting is on (check: gh api repos/<owner>/<repo>/actions/oidc/customization/sub)."
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
