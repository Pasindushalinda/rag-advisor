output "endpoint" {
  value = azurerm_cognitive_account.ai.endpoint
}

output "chat_deployment" {
  value = azurerm_cognitive_deployment.chat.name
}

output "embedding_deployment" {
  value = azurerm_cognitive_deployment.embedding.name
}

output "primary_key" {
  value     = azurerm_cognitive_account.ai.primary_access_key
  sensitive = true
}

output "langfuse_url" {
  value = "https://${azurerm_container_app.langfuse.ingress[0].fqdn}"
}

output "langfuse_admin_email" {
  value = "admin@admin.com"
}

# sensitive hides it from plan/apply logs; read it with `terraform output -raw langfuse_admin_password`
output "langfuse_admin_password" {
  value     = random_password.langfuse_admin_password.result
  sensitive = true
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.pg.fqdn
}

output "qdrant_url" {
  description = "Public HTTPS URL"
  value       = "https://${azurerm_container_app.qdrant.ingress[0].fqdn}"
}

# sensitive hides it from plan/apply logs; read it with `terraform output -raw qdrant_api_key`
output "qdrant_api_key" {
  value     = random_password.qdrant_api_key.result
  sensitive = true
}

output "api_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "acr_name" {
  value = azurerm_container_registry.acr.name
}

# Bearer tokens for the API; read with `terraform output -raw service_api_key`
output "service_api_key" {
  value     = random_password.service_api_key.result
  sensitive = true
}

output "admin_api_key" {
  value     = random_password.admin_api_key.result
  sensitive = true
}




# Langfuse project keys created by headless init; used by scripts/seed_deployed.sh
output "langfuse_public_key" {
  value     = local.langfuse_public_key
  sensitive = true
}

output "langfuse_secret_key" {
  value     = local.langfuse_secret_key
  sensitive = true
}

output "chat_ui_url" {
  value = "https://${azurerm_container_app.chat_ui.ingress[0].fqdn}"
}
