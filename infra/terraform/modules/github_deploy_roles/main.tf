# GitHub Actions OIDC deploy roles (QDEV-890, SG-I3). No long-lived keys anywhere.
# plan role: read-only, assumable from pull requests. apply role: write, assumable only
# from jobs bound to the GitHub Environment (prod environment requires approval).

locals {
  oidc_url          = "token.actions.githubusercontent.com"
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${var.account_id}:oidc-provider/${local.oidc_url}"
  prefix            = "${var.project}-${var.environment}"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "${local.oidc_url}:sub"
      values   = var.plan_subjects
    }
  }
}

data "aws_iam_policy_document" "apply_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:sub"
      values   = ["${var.github_subject_prefix}:environment:${var.apply_environment}"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name               = "${local.prefix}-tf-plan"
  assume_role_policy = data.aws_iam_policy_document.plan_trust.json
}

resource "aws_iam_role_policy_attachment" "plan_read" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "state_access" {
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.state_bucket_arn}/*"]
  }
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]
  }
}

resource "aws_iam_role_policy" "plan_state" {
  name   = "state-access"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.state_access.json
}

resource "aws_iam_role" "apply" {
  name               = "${local.prefix}-tf-apply"
  assume_role_policy = data.aws_iam_policy_document.apply_trust.json
}

resource "aws_iam_role_policy" "apply_state" {
  name   = "state-access"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.state_access.json
}

# Apply permissions are limited to this project's resource names.
# NOTE: widen per story (Glue, Lambda, Step Functions...) when SG-I4..I8 land.
data "aws_iam_policy_document" "apply" {
  statement {
    sid       = "ProjectBuckets"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::${var.project}-${var.environment}-*", "arn:aws:s3:::${var.project}-${var.environment}-*/*"]
  }
  statement {
    sid       = "ProjectRolesAndPolicies"
    actions   = ["iam:*"]
    resources = ["arn:aws:iam::${var.account_id}:role/${local.prefix}-*", "arn:aws:iam::${var.account_id}:policy/${local.prefix}-*"]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/project"
      values   = [var.project]
    }
  }
  statement {
    sid       = "KmsCreate"
    actions   = ["kms:CreateKey", "kms:CreateAlias", "kms:ListAliases", "kms:TagResource"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "apply" {
  name   = "apply-project-resources"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply.json
}
