# Registry for the API image. Basic SKU is the cheapest (about $5/month). Admin credentials are
# used for pulls because a role assignment (AcrPull) would need roleAssignments/write.
resource "azurerm_container_registry" "acr" {
  name                = "ragacr57o80"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = true
}

# Keys the callers (chat UI users, admins) must present; see backend/auth.py.
resource "random_password" "service_api_key" {
  length  = 48
  special = false
}

resource "random_password" "admin_api_key" {
  length  = 48
  special = false
}

resource "azurerm_container_app" "api" {
  name                         = "rag-api"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = data.azurerm_resource_group.rg.name
  revision_mode                = "Single"

  registry {
    server               = azurerm_container_registry.acr.login_server
    username             = azurerm_container_registry.acr.admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.acr.admin_password
  }
  secret {
    name = "database-url"
    # sslmode=require: Azure Postgres rejects unencrypted connections. Aegra maps it to asyncpg's ssl param.
    value = "postgresql://${urlencode(azurerm_postgresql_flexible_server.pg.administrator_login)}:${urlencode(random_password.postgres_admin.result)}@${azurerm_postgresql_flexible_server.pg.fqdn}:5432/${azurerm_postgresql_flexible_server_database.aegra_db.name}?sslmode=require"
  }
  secret {
    name  = "redis-url"
    value = "redis://:${random_password.aegra_redis.result}@${azurerm_container_app.aegra_redis.name}:6379/0"
  }
  secret {
    name  = "qdrant-api-key"
    value = random_password.qdrant_api_key.result
  }
  secret {
    name  = "ai-key"
    value = azurerm_cognitive_account.ai.primary_access_key
  }
  secret {
    name  = "langfuse-public-key"
    value = local.langfuse_public_key
  }
  secret {
    name  = "langfuse-secret-key"
    value = local.langfuse_secret_key
  }
  secret {
    name  = "service-api-key"
    value = random_password.service_api_key.result
  }
  secret {
    name  = "admin-api-key"
    value = random_password.admin_api_key.result
  }

  template {
    # Always one replica: Aegra runs graph runs in-process, so scaling to zero or out would cut
    # runs off. Set min_replicas to 0 to save money if cold starts of ~1 minute are acceptable.
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "api"
      image  = "${azurerm_container_registry.acr.login_server}/rag-api:${var.api_image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name  = "REDIS_BROKER_ENABLED"
        value = "true"
      }
      env {
        name        = "REDIS_URL"
        secret_name = "redis-url"
      }
      env {
        name  = "QDRANT_URL"
        value = "https://${azurerm_container_app.qdrant.ingress[0].fqdn}"
      }
      env {
        name        = "QDRANT_API_KEY"
        secret_name = "qdrant-api-key"
      }
      env {
        name  = "CHAT_MODEL_ENDPOINT"
        value = "https://${var.ai_account_name}.services.ai.azure.com/openai/v1/"
      }
      env {
        name        = "CHAT_MODEL_API_KEY"
        secret_name = "ai-key"
      }
      env {
        name  = "CHAT_MODEL_NAME"
        value = azurerm_cognitive_deployment.chat.name
      }
      env {
        name  = "EMBEDDING_MODEL_ENDPOINT"
        value = "https://${var.ai_account_name}.services.ai.azure.com/openai/v1/"
      }
      env {
        name        = "EMBEDDING_MODEL_API_KEY"
        secret_name = "ai-key"
      }
      env {
        name  = "EMBEDDING_MODEL_NAME"
        value = azurerm_cognitive_deployment.embedding.name
      }
      # Market research agent (share prices, news) uses the same Azure chat deployment through the
      # generic "openai" provider, so no extra vendor key is needed. GPT-5 models reject any
      # temperature but 1 and count reasoning tokens against max tokens, hence the overrides.
      env {
        name  = "LLM_PROVIDER"
        value = "openai"
      }
      env {
        name  = "LLM_MODEL"
        value = azurerm_cognitive_deployment.chat.name
      }
      env {
        name  = "LLM_BASE_URL"
        value = "https://${var.ai_account_name}.services.ai.azure.com/openai/v1/"
      }
      env {
        name        = "LLM_API_KEY"
        secret_name = "ai-key"
      }
      env {
        name  = "LLM_TEMPERATURE"
        value = "1"
      }
      env {
        name  = "LLM_MAX_TOKENS"
        value = "4096"
      }
      env {
        name  = "LANGFUSE_HOST"
        value = "https://${azurerm_container_app.langfuse.ingress[0].fqdn}"
      }
      env {
        name        = "LANGFUSE_PUBLIC_KEY"
        secret_name = "langfuse-public-key"
      }
      env {
        name        = "LANGFUSE_SECRET_KEY"
        secret_name = "langfuse-secret-key"
      }
      env {
        name        = "SERVICE_API_KEY"
        secret_name = "service-api-key"
      }
      env {
        name        = "ADMIN_API_KEY"
        secret_name = "admin-api-key"
      }

      # TCP, not HTTP: aegra's custom-route auth (backend/aegra.json) makes /health answer 401
      # without a bearer token, and probe headers cannot carry a secret.
      startup_probe {
        transport               = "TCP"
        port                    = 8000
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 10
      }

      liveness_probe {
        transport               = "TCP"
        port                    = 8000
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  depends_on = [
    azurerm_postgresql_flexible_server_firewall_rule.allow_azure_services,
    azurerm_container_app.aegra_redis,
  ]
}
