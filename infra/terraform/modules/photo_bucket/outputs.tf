output "bucket_name" {
  value       = aws_s3_bucket.photos.bucket
  description = "Photo bucket name."
}

output "bucket_arn" {
  value       = aws_s3_bucket.photos.arn
  description = "Photo bucket ARN."
}

output "kms_key_arn" {
  value       = aws_kms_key.photos.arn
  description = "CMK encrypting the bucket."
}

output "copy_job_role_arn" {
  value       = aws_iam_role.copy_job.arn
  description = "Role the Imgix-to-S3 copy job assumes (SG-B5)."
}

output "read_photos_policy_arn" {
  value       = aws_iam_policy.read_photos.arn
  description = "Managed policy granting read on photos; attach to the scoring role (SG-I1)."
}
