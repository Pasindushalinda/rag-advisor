# Existing account that also holds the Terraform state (see backend.tf); not managed here.
data "azurerm_storage_account" "shared" {
  name                = "ragtf57o80"
  resource_group_name = data.azurerm_resource_group.rg.name
}

# Azure Files, because Container Apps cannot mount Blob and Qdrant has no Blob backend.
# The quota is only a cap: Standard shares bill for stored data, and we expect under 100 MB.
resource "azurerm_storage_share" "qdrant" {
  name               = "qdrant-storage"
  storage_account_id = data.azurerm_storage_account.shared.id
  quota              = 1
}

resource "azurerm_container_app_environment_storage" "qdrant" {
  name                         = "qdrant-storage"
  container_app_environment_id = azurerm_container_app_environment.env.id
  account_name                 = data.azurerm_storage_account.shared.name
  share_name                   = azurerm_storage_share.qdrant.name
  access_key                   = data.azurerm_storage_account.shared.primary_access_key
  access_mode                  = "ReadWrite"
}

resource "random_password" "qdrant_api_key" {
  length  = 48
  special = false
}

resource "azurerm_container_app" "qdrant" {
  name                         = "rag-qdrant"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = data.azurerm_resource_group.rg.name
  revision_mode                = "Single"

  secret {
    name  = "qdrant-api-key"
    value = random_password.qdrant_api_key.result
  }

  template {
    # Scale to zero is the cheapest option; the first request after idle waits for a cold
    # start, so clients need generous timeouts/retries. Max 1: Qdrant storage is not safe
    # for concurrent writers.
    min_replicas = 0
    max_replicas = 1

    volume {
      name         = "storage"
      storage_name = azurerm_container_app_environment_storage.qdrant.name
      storage_type = "AzureFile"
      # nobrl: SMB byte-range locks break Qdrant's file handling; mfsymlinks: snapshots need symlinks.
      mount_options = "nobrl,mfsymlinks"
    }

    container {
      name = "qdrant"
      # Pinned, unlike docker-compose's :latest, so a restart never upgrades Qdrant against existing storage.
      image = "docker.io/qdrant/qdrant:v1.19.2"
      # Minimum size: the collection is expected to stay under 100 MB.
      cpu    = 0.25
      memory = "0.5Gi"

      # Same as compose's production.yaml (log_level: INFO). An env var avoids mounting a config
      # directory over the image's default config.
      env {
        name  = "QDRANT__LOG_LEVEL"
        value = "INFO"
      }

      env {
        name        = "QDRANT__SERVICE__API_KEY"
        secret_name = "qdrant-api-key"
      }

      volume_mounts {
        name = "storage"
        path = "/qdrant/storage"
      }

      startup_probe {
        transport               = "TCP"
        port                    = 6333
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 10
      }

      liveness_probe {
        transport               = "TCP"
        port                    = 6333
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  # Public HTTPS URL, protected by the API key (sent as the `api-key` header).
  ingress {
    external_enabled = true
    target_port      = 6333
    transport        = "http"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
