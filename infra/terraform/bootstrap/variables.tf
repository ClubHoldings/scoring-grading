variable "environment" {
  type        = string
  description = "dev or prod."
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "account_id" {
  type        = string
  description = "Target AWS account. Provider refuses any other account. Supply via TF_VAR_account_id."
}

variable "region" {
  type        = string
  description = "AWS region."
}

variable "project" {
  type    = string
  default = "scoring-grading"
}

variable "owner" {
  type        = string
  description = "Owning team or person for the owner tag."
}

variable "cost_centre" {
  type        = string
  description = "Cost centre for the cost_centre tag."
}

variable "github_subject_prefix" {
  type        = string
  default     = "repo:ClubHoldings@1420122/scoring-grading@1469854041"
  description = "OIDC subject prefix of ClubHoldings/scoring-grading (numeric IDs are public, not secret). Changes if the repo is recreated or transferred."
}

variable "create_oidc_provider" {
  type    = bool
  default = true
}
