resource "random_password" "postgres_admin" {
  length      = 32
  special     = false
  min_upper   = 2
  min_lower   = 2
  min_numeric = 2
}

resource "azurerm_postgresql_flexible_server" "pg" {
  name                = "rag-pg-57o80"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = var.location
  version             = "16"

  administrator_login    = var.postgres_admin_username
  administrator_password = random_password.postgres_admin.result

  sku_name   = "B_Standard_B1ms"
  storage_mb = 32768

  backup_retention_days         = 7
  public_network_access_enabled = true

  lifecycle {
    # Azure picks an availability zone itself; without this Terraform may try to change it
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "langfuse_db" {
  name      = "langfuse-db"
  server_id = azurerm_postgresql_flexible_server.pg.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Shares the Langfuse server instead of a dedicated one (compose runs a separate aegra-db
# container): one B1ms server is enough for both and halves the cost.
resource "azurerm_postgresql_flexible_server_database" "aegra_db" {
  name      = "aegra-db"
  server_id = azurerm_postgresql_flexible_server.pg.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Postgres blocks all connections by default. 0.0.0.0 - 0.0.0.0 is Azure's special rule for
# "allow Azure services". Demo-grade: the long random password and SSL are the protection.
resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.pg.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}
