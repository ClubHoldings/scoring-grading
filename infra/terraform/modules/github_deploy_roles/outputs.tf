output "plan_role_arn" {
  value       = aws_iam_role.plan.arn
  description = "Role for plan jobs."
}

output "apply_role_arn" {
  value       = aws_iam_role.apply.arn
  description = "Role for apply jobs."
}
