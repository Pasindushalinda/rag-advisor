provider "azurerm" {
  features {
    cognitive_account {
      # Without this, a deleted AI account keeps its name reserved for ~48 days
      # and blocks re-creating it after a destroy.
      purge_soft_delete_on_destroy = true
    }
  }

  subscription_id = var.subscription_id
}
