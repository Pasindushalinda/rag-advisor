# Agent Chat UI (Next.js), built from ../chat-ui. The browser talks to the API directly: users enter
# the API URL, the graph ID and a key in the UI's own form, because the image is built without
# NEXT_PUBLIC_* values (see chat-ui/Dockerfile). So this app needs no env vars and no secrets
# of its own, only the registry pull credential.
resource "azurerm_container_app" "chat_ui" {
  name                         = "rag-chat-ui"
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

  template {
    # Scale to zero: the UI is stateless (all state lives in the API), so an idle UI costs nothing
    # and a cold start only takes a few seconds. Unlike the API, it can safely run several replicas.
    min_replicas = 0
    max_replicas = 2

    container {
      name   = "chat-ui"
      image  = "${azurerm_container_registry.acr.login_server}/rag-chat-ui:${var.chat_ui_image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      startup_probe {
        transport               = "HTTP"
        port                    = 3000
        path                    = "/"
        initial_delay           = 5
        interval_seconds        = 5
        timeout                 = 5
        failure_count_threshold = 10
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 3000
        path                    = "/"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 3000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
