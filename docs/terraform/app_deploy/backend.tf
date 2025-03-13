# Setup remote terraform state file in Google Cloud Storage
# State locking is enabled by default when using Google Cloud Storage as a backend.
terraform {
  backend "gcs" {
    bucket = "tfstate-wandisco-public-384719"
    prefix = "doit-marketplace"
  }
}
