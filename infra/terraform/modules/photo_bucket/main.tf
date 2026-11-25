# Photo bucket for scoring/grading (QDEV-889, SG-I2).
#
# Key layout (content hash makes copies idempotent and detects changed photos):
#   original/<home_id>/<sha256>.<ext>            written once by the copy job
#   resized/<home_id>/<sha256>_<width>.<ext>     regenerable; expires via lifecycle
# Builder lives in grade/photo_keys.py; keep the two in sync.

locals {
  bucket_name = "${var.project}-${var.environment}-photos-${var.account_id}"
}

resource "aws_kms_key" "photos" {
  description             = "${var.project} ${var.environment} photo bucket encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "photos" {
  name          = "alias/${var.project}-${var.environment}-photos"
  target_key_id = aws_kms_key.photos.key_id
}

resource "aws_s3_bucket" "photos" {
  bucket        = local.bucket_name
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "photos" {
  bucket                  = aws_s3_bucket.photos.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "photos" {
  bucket = aws_s3_bucket.photos.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "photos" {
  bucket = aws_s3_bucket.photos.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "photos" {
  bucket = aws_s3_bucket.photos.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.photos.arn
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "photos" {
  bucket = aws_s3_bucket.photos.id

  rule {
    id     = "expire-resized-copies"
    status = "Enabled"
    filter {
      prefix = "resized/"
    }
    expiration {
      days = var.resized_copy_expiry_days
    }
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiry_days
    }
  }

  rule {
    id     = "cleanup-old-versions-and-uploads"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiry_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.photos]
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.photos.arn, "${aws_s3_bucket.photos.arn}/*"]
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

resource "aws_s3_bucket_policy" "photos" {
  bucket     = aws_s3_bucket.photos.id
  policy     = data.aws_iam_policy_document.tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.photos]
}

# Access is identity-based only: nothing else gets a grant, so "nothing else can" holds
# (the bucket policy grants no principal; account admins are not exempt from IAM, but
# no other role in this stack receives photo access).

# Copy job: write originals and resized copies; no read, no delete.
data "aws_iam_policy_document" "copy_job_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = var.copy_job_trusted_services
    }
  }
}

data "aws_iam_policy_document" "copy_job" {
  statement {
    sid       = "WritePhotos"
    actions   = ["s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.photos.arn}/original/*", "${aws_s3_bucket.photos.arn}/resized/*"]
  }
  statement {
    sid       = "ExistenceCheckForIdempotency"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.photos.arn]
  }
  statement {
    sid       = "EncryptWithBucketKey"
    actions   = ["kms:GenerateDataKey", "kms:Encrypt"]
    resources = [aws_kms_key.photos.arn]
  }
}

resource "aws_iam_role" "copy_job" {
  name               = "${var.project}-${var.environment}-photo-copy-job"
  assume_role_policy = data.aws_iam_policy_document.copy_job_trust.json
}

resource "aws_iam_role_policy" "copy_job" {
  name   = "write-photos"
  role   = aws_iam_role.copy_job.id
  policy = data.aws_iam_policy_document.copy_job.json
}

# Read policy for the AI/scoring role. Created here, attached by the role module (SG-I1)
# so the bucket module does not depend on the Bedrock work.
data "aws_iam_policy_document" "read_photos" {
  statement {
    sid       = "ReadPhotos"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.photos.arn}/original/*", "${aws_s3_bucket.photos.arn}/resized/*"]
  }
  statement {
    sid       = "ListPhotos"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.photos.arn]
  }
  statement {
    sid       = "DecryptPhotos"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.photos.arn]
  }
}

resource "aws_iam_policy" "read_photos" {
  name   = "${var.project}-${var.environment}-photos-read"
  policy = data.aws_iam_policy_document.read_photos.json
}
