terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
  # Bucket and account are passed at init (-backend-config) so no account ID is committed.
  backend "s3" {
    key          = "scoring-grading/terraform.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}
