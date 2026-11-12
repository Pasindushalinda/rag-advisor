# Read-only lookup: Terraform never creates or destroys the resource group.
data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

resource "azurerm_cognitive_account" "ai" {
  name                  = var.ai_account_name
  location              = var.location
  resource_group_name   = data.azurerm_resource_group.rg.name
  kind                  = "AIServices"
  sku_name              = "S0"
  custom_subdomain_name = var.ai_account_name

  # Azure enables these on AIServices (Foundry) accounts; declare them so Terraform
  # does not try to revert them, which would force replacing the account.
  project_management_enabled = true

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = "chat"
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = "OpenAI"
    name    = var.chat_model_name
    version = var.chat_model_version
  }

  sku {
    name     = var.chat_sku
    capacity = var.chat_capacity
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = "embedding"
  cognitive_account_id = azurerm_cognitive_account.ai.id

  model {
    format  = "OpenAI"
    name    = var.embedding_model_name
    version = var.embedding_model_version
  }

  sku {
    name     = var.embedding_sku
    capacity = var.embedding_capacity
  }

  # Deployments on one account must be created one at a time
  depends_on = [azurerm_cognitive_deployment.chat]
}
