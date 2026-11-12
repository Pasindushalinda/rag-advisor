variable "subscription_id" {
  type        = string
  description = "Azure subscription ID"
}

variable "resource_group_name" {
  type        = string
  description = "Existing resource group. Terraform only reads it and never manages or deletes it."
  default     = "rg-rag-dev"
}

variable "ai_account_name" {
  type        = string
  description = "Globally unique, lowercase. Also becomes the endpoint subdomain."
}

# The subscription quota is shared with many other pods. DataZoneStandard has free
# quota while GlobalStandard for these models is nearly used up.
# gpt-5.4 has zero DataZoneStandard quota on this subscription; gpt-5.4-mini has 200 and is cheaper.
variable "chat_model_name" {
  type    = string
  default = "gpt-5.4-mini"
}

variable "chat_model_version" {
  type    = string
  default = "2026-03-17"
}

variable "chat_sku" {
  type    = string
  default = "DataZoneStandard"
}

variable "chat_capacity" {
  type        = number
  description = "Thousands of tokens per minute. A cap, not a cost (billing is per token)."
  default     = 100
}

variable "embedding_model_name" {
  type    = string
  default = "text-embedding-3-small"
}

variable "embedding_model_version" {
  type    = string
  default = "1"
}

variable "embedding_sku" {
  type    = string
  default = "DataZoneStandard"
}

variable "embedding_capacity" {
  type        = number
  description = "Thousands of tokens per minute. A cap, not a cost (billing is per token). 30 caused 429s while ingesting filings."
  default     = 300
}

# ---- Langfuse stage ----

variable "location" {
  type        = string
  description = "Region for the Container Apps environment, Postgres, and AI"
  default     = "southindia"
}

variable "container_app_environment_name" {
  type    = string
  default = "rag-env"
}

variable "postgres_admin_username" {
  type    = string
  default = "pgadmin"
}

variable "langfuse_app_name" {
  type    = string
  default = "rag-langfuse"
}

variable "langfuse_external_ingress" {
  type        = bool
  description = "true = public HTTPS URL. false = reachable only from inside the Container Apps environment."
  default     = true
}

variable "langfuse_allowed_ip_ranges" {
  type        = list(string)
  description = "Optional CIDR allow-list for the public URL (e.g. [\"203.0.113.0/24\"]). Empty = open to the internet."
  default     = []
}

# ---- API stage ----

variable "api_image_tag" {
  type        = string
  description = "Tag of rag-api in the registry. Bump it for every new build so Container Apps creates a new revision."
  default     = "v1"
}

variable "chat_ui_image_tag" {
  type        = string
  description = "Tag of rag-chat-ui in the registry. Bump it for every new build so Container Apps creates a new revision."
  default     = "v1"
}
