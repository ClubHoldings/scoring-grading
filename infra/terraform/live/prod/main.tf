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

module "photo_bucket" {
  source        = "../../modules/photo_bucket"
  project       = var.project
  environment   = var.environment
  account_id    = var.account_id
  force_destroy = false
}

output "photo_bucket_name" {
  value = module.photo_bucket.bucket_name
}
