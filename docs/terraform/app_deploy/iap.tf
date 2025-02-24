resource "google_project_service_identity" "iap_gsi" {
  provider = google-beta
  service  = "iap.googleapis.com"
}

resource "google_iap_client" "iap_client" {
  display_name = var.iap_client_display_name
  brand        = "projects/676221988800/brands/676221988800"
}
