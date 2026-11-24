variable "project" {
  type        = string
  description = "Project slug used as name prefix."
}

variable "environment" {
  type        = string
  description = "Environment name (dev, prod)."
}

variable "account_id" {
  type        = string
  description = "AWS account ID; part of the bucket name because S3 names are global."
}

variable "resized_copy_expiry_days" {
  type        = number
  default     = 30
  description = "Days before resized copies under resized/ expire. They are regenerable from original/."
}

variable "noncurrent_version_expiry_days" {
  type        = number
  default     = 30
  description = "Days before overwritten object versions are removed."
}

variable "copy_job_trusted_services" {
  type        = list(string)
  default     = ["ecs-tasks.amazonaws.com", "lambda.amazonaws.com"]
  description = "NOTE: compute is undecided until SG-I4 (QDEV-891); narrow to one service then."
}

variable "force_destroy" {
  type        = bool
  default     = false
  description = "Allow destroying a non-empty bucket. Must stay false in prod."
}
