# Container app instead of Azure Cache for Redis: Aegra only uses it as a short-lived broker,
# so a plain redis container (same as compose's aegra-redis) is far cheaper than a managed cache.
resource "random_password" "aegra_redis" {
  length  = 32
  special = false
}

resource "azurerm_container_app" "aegra_redis" {
  name                         = "rag-aegra-redis"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = data.azurerm_resource_group.rg.name
  revision_mode                = "Single"

  secret {
    name  = "redis-password"
    value = random_password.aegra_redis.result
  }

  template {
    # Always on: the API needs the broker for every run, and TCP traffic cannot wake a
    # scaled-to-zero app the way HTTP does. Max 1: data lives in memory of a single instance.
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "redis"
      image  = "docker.io/library/redis:7-alpine"
      cpu    = 0.25
      memory = "0.5Gi"

      # Compose runs Redis without a password because it is only on the private docker network;
      # here the environment is shared, so require one. sh -c is needed to expand the env var.
      command = ["sh", "-c", "exec redis-server --requirepass \"$REDIS_PASSWORD\""]

      env {
        name        = "REDIS_PASSWORD"
        secret_name = "redis-password"
      }

      startup_probe {
        transport               = "TCP"
        port                    = 6379
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 10
      }

      liveness_probe {
        transport               = "TCP"
        port                    = 6379
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  # Internal only: external TCP ingress needs a custom VNet, and nothing outside the
  # environment should reach the broker. Other apps connect to rag-aegra-redis:6379.
  ingress {
    external_enabled = false
    target_port      = 6379
    exposed_port     = 6379
    transport        = "tcp"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
