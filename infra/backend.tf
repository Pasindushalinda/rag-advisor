terraform {
  backend "azurerm" {
    resource_group_name  = "rg-rag-dev"
    storage_account_name = "ragtf57o80"
    container_name       = "tfstate"
    key                  = "terraform.tfstate"
  }
}
