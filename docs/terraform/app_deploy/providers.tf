provider "google-beta" {
  project = var.project_id
}
provider "google" {
  project = var.project_id
}


#Get an access token for the wandisco-google SA
data "google_service_account_access_token" "prod_token" {
  # target_service_account = data.google_service_account.doit_easily_backend_integration_sa.email
  target_service_account = local.service_account_email
  scopes                 = ["userinfo-email", "cloud-platform"]
  lifetime               = "1200s"
}

#Provider used to apply resources as the wandisco-google SA
provider "google" {
  alias        = "prod_impersonation"
  access_token = data.google_service_account_access_token.prod_token.access_token
}


