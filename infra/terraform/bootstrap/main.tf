# One-time per account, applied by a human with admin rights, local state (gitignored).
# Creates the remote-state bucket, the GitHub OIDC provider and the deploy roles that the
# pipeline then uses for everything else. Chicken-and-egg: the pipeline cannot create these.
#   terraform init && terraform apply -var-file=../live/<env>/bootstrap.tfvars
# State of this stack is tiny and rebuildable; do not commit it.

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = {
      project     = var.project
      owner       = var.owner
      environment = var.environment
      cost_centre = var.cost_centre
      managed_by  = "terraform"
    }
  }
}

locals {
  state_bucket = "${var.project}-tfstate-${var.environment}-${var.account_id}"
  # prod applies only from the prod branch behind approval; dev from the dev branch.
  plan_subjects = ["repo:${var.github_repo}:pull_request", "repo:${var.github_repo}:ref:refs/heads/*", "repo:${var.github_repo}:environment:*"]
}

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "state_tls" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.state_tls.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

module "deploy_roles" {
  source               = "../modules/github_deploy_roles"
  project              = var.project
  environment          = var.environment
  account_id           = var.account_id
  github_repo          = var.github_repo
  apply_environment    = var.environment
  plan_subjects        = local.plan_subjects
  state_bucket_arn     = aws_s3_bucket.state.arn
  create_oidc_provider = var.create_oidc_provider
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "plan_role_arn" {
  value = module.deploy_roles.plan_role_arn
}

output "apply_role_arn" {
  value = module.deploy_roles.apply_role_arn
}
