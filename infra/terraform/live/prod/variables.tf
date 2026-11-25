variable "account_id" {
  type        = string
  description = "Target AWS account; provider refuses any other. Supply via TF_VAR_account_id (GitHub environment variable in CI)."
}

variable "region" {
  type        = string
  description = "AWS region."
}

variable "environment" {
  type        = string
  description = "Environment name."
}

variable "project" {
  type    = string
  default = "scoring-grading"
}

variable "owner" {
  type        = string
  description = "Owner tag."
}

variable "cost_centre" {
  type        = string
  description = "Cost centre tag."
}
