# Shared hosting space for all our containers: Langfuse, Qdrant, Redis, the API and the chat UI.
# No logs_destination on purpose (no Log Analytics): logs are live-stream only, with no history.
resource "azurerm_container_app_environment" "env" {
  name                = var.container_app_environment_name
  location            = var.location
  resource_group_name = data.azurerm_resource_group.rg.name
}

data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {
  subscription_id = var.subscription_id
}

# Access policy for whoever runs Terraform (the signed-in identity), so it can write the secrets.
resource "azurerm_key_vault" "kv" {
  name                       = "kv-rag-57o80"
  location                   = var.location
  resource_group_name        = data.azurerm_resource_group.rg.name
  tenant_id                  = data.azurerm_subscription.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = false
  soft_delete_retention_days = 7
  # Azure does not allow turning purge protection off once enabled, and a destroyed vault's
  # name stays reserved for the retention period.
  purge_protection_enabled = true

  access_policy {
    tenant_id          = data.azurerm_subscription.current.tenant_id
    object_id          = data.azurerm_client_config.current.object_id
    secret_permissions = ["Get", "List", "Set", "Delete", "Recover"]
    key_permissions    = ["Get", "List", "Create", "Delete", "Update", "Import", "Recover"]
  }
}
