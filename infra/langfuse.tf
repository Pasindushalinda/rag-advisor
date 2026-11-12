# Written by Terraform from the Postgres admin login. A fresh subscription has no pre-filled
# vault, so reading these as data sources would fail the first plan.
resource "azurerm_key_vault_secret" "langfuse_db_user" {
  name         = "langfuse-db-user"
  value        = azurerm_postgresql_flexible_server.pg.administrator_login
  key_vault_id = azurerm_key_vault.kv.id
}

resource "azurerm_key_vault_secret" "langfuse_db_password" {
  name         = "langfuse-db-password"
  value        = random_password.postgres_admin.result
  key_vault_id = azurerm_key_vault.kv.id
}

resource "random_password" "langfuse_nextauth_secret" {
  length  = 48
  special = false
}

resource "random_password" "langfuse_salt" {
  length  = 32
  special = false
}

resource "random_password" "langfuse_admin_password" {
  length      = 24
  special     = false
  min_upper   = 2
  min_lower   = 2
  min_numeric = 2
}

# Langfuse creates this project and its API keys on first start (headless init), so the API gets
# working keys without a manual step in the UI.
resource "random_password" "langfuse_project_key" {
  length  = 32
  special = false
}

resource "random_password" "langfuse_project_secret" {
  length  = 32
  special = false
}

locals {
  langfuse_public_key = "pk-lf-${random_password.langfuse_project_key.result}"
  langfuse_secret_key = "sk-lf-${random_password.langfuse_project_secret.result}"
}

resource "azurerm_container_app" "langfuse" {
  name                         = var.langfuse_app_name
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = data.azurerm_resource_group.rg.name
  revision_mode                = "Single"

  secret {
    name = "database-url"
    # urlencode: the credentials come from the Postgres admin login and may contain characters reserved in URLs.
    value = "postgresql://${urlencode(azurerm_key_vault_secret.langfuse_db_user.value)}:${urlencode(azurerm_key_vault_secret.langfuse_db_password.value)}@${azurerm_postgresql_flexible_server.pg.fqdn}:5432/${azurerm_postgresql_flexible_server_database.langfuse_db.name}?sslmode=require"
  }

  secret {
    name  = "nextauth-secret"
    value = random_password.langfuse_nextauth_secret.result
  }

  secret {
    name  = "salt"
    value = random_password.langfuse_salt.result
  }

  secret {
    name  = "project-public-key"
    value = local.langfuse_public_key
  }

  secret {
    name  = "project-secret-key"
    value = local.langfuse_secret_key
  }

  secret {
    name  = "password"
    value = random_password.langfuse_admin_password.result
  }

  template {
    # Always exactly one replica: prompts are fetched live by the API, so scale-to-zero would
    # make chats fail, and two replicas could run the startup migration twice.
    min_replicas = 1
    max_replicas = 1

    container {
      name  = "langfuse"
      image = "docker.io/langfuse/langfuse:2"
      # Smallest size that still fits Langfuse v2 (Next.js). Always-on memory is the main cost here;
      # if the first start is OOM-killed during migrations, raise to 1.0 / "2Gi".
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name = "NEXTAUTH_URL"
        # Internal ingress gets an ".internal" label in its address; a self-reference to the app's
        # own fqdn would be circular, so the URL is built from the environment domain instead.
        value = "https://${var.langfuse_app_name}${var.langfuse_external_ingress ? "" : ".internal"}.${azurerm_container_app_environment.env.default_domain}"
      }
      env {
        name        = "NEXTAUTH_SECRET"
        secret_name = "nextauth-secret"
      }
      env {
        name        = "SALT"
        secret_name = "salt"
      }
      env {
        name  = "TELEMETRY_ENABLED"
        value = "false"
      }
      # Only the headless-init user can log in
      env {
        name  = "AUTH_DISABLE_SIGNUP"
        value = "true"
      }

      # Langfuse only creates the init user when an init organization is also defined.
      env {
        name  = "LANGFUSE_INIT_ORG_ID"
        value = "investor-advisor-org"
      }
      env {
        name  = "LANGFUSE_INIT_ORG_NAME"
        value = "InvestorAdvisor"
      }
      env {
        name  = "LANGFUSE_INIT_PROJECT_ID"
        value = "investor-advisor"
      }
      env {
        name  = "LANGFUSE_INIT_PROJECT_NAME"
        value = "InvestorAdvisor"
      }
      env {
        name        = "LANGFUSE_INIT_PROJECT_PUBLIC_KEY"
        secret_name = "project-public-key"
      }
      env {
        name        = "LANGFUSE_INIT_PROJECT_SECRET_KEY"
        secret_name = "project-secret-key"
      }
      env {
        name  = "LANGFUSE_INIT_USER_EMAIL"
        value = "admin@admin.com"
      }
      env {
        name  = "LANGFUSE_INIT_USER_NAME"
        value = "Admin"
      }
      env {
        name        = "LANGFUSE_INIT_USER_PASSWORD"
        secret_name = "password"
      }

      # The first start applies database migrations, which can take minutes.
      # Container Apps caps failure_count_threshold at 10, so 10 x 30s allows about 5 minutes.
      startup_probe {
        transport               = "HTTP"
        port                    = 3000
        path                    = "/api/public/health"
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 10
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 3000
        path                    = "/api/public/health"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  ingress {
    external_enabled = var.langfuse_external_ingress
    target_port      = 3000
    transport        = "auto"

    dynamic "ip_security_restriction" {
      for_each = var.langfuse_allowed_ip_ranges
      content {
        name             = "allow-${ip_security_restriction.key}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
      }
    }

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  depends_on = [azurerm_postgresql_flexible_server_firewall_rule.allow_azure_services]
}
