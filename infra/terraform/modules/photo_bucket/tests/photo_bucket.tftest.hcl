# Plan-level tests against the real module code; only the AWS API is mocked.
mock_provider "aws" {
  # The real policy documents are rendered locally by Terraform; the mock only needs valid JSON.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"s3:ListBucket\",\"Resource\":\"*\"}]}"
    }
  }
}

variables {
  project     = "scoring-grading"
  environment = "dev"
  account_id  = "123456789012"
}

run "bucket_is_private_encrypted_and_named_per_env" {
  command = plan

  assert {
    condition     = aws_s3_bucket.photos.bucket == "scoring-grading-dev-photos-123456789012"
    error_message = "bucket name must embed project, environment and account"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.photos.block_public_acls && aws_s3_bucket_public_access_block.photos.block_public_policy && aws_s3_bucket_public_access_block.photos.ignore_public_acls && aws_s3_bucket_public_access_block.photos.restrict_public_buckets
    error_message = "all four public access blocks must be on"
  }
  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.photos.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "aws:kms"
    error_message = "bucket must use SSE-KMS"
  }
  assert {
    condition     = aws_kms_key.photos.enable_key_rotation
    error_message = "CMK rotation must be on"
  }
}

run "resized_copies_expire_by_prefix" {
  command = plan

  assert {
    condition     = [for r in aws_s3_bucket_lifecycle_configuration.photos.rule : r if r.id == "expire-resized-copies"][0].filter[0].prefix == "resized/"
    error_message = "resized expiry must be scoped to resized/"
  }
  assert {
    condition     = [for r in aws_s3_bucket_lifecycle_configuration.photos.rule : r if r.id == "expire-resized-copies"][0].expiration[0].days == 30
    error_message = "default resized expiry is 30 days"
  }
}

run "prod_env_changes_names" {
  command = plan

  variables {
    environment = "prod"
    account_id  = "999999999999"
  }

  assert {
    condition     = aws_s3_bucket.photos.bucket == "scoring-grading-prod-photos-999999999999"
    error_message = "per-environment naming must change with inputs"
  }
}
