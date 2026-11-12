# Deploying to Azure

Deploys the whole stack (Langfuse, Qdrant, Redis, the API, the chat UI, Postgres, Azure AI models) with
Terraform from `infra/`. Run commands from the repo root unless a step says `infra/`.

Resource names below (`rg-rag-dev`, `ragtf57o80`, `ragacr57o80`, `rag-pg-57o80`) are the ones in
`infra/`. If you change them in the `.tf` files, change them here too.

## 0. One-time setup

```bash
brew tap hashicorp/tap && brew install hashicorp/tap/terraform
az login
az account set --subscription <subscription-id>
```

You also need `uv` and a working `backend/.venv`, because the seed step uses it.

## 1. Create what Terraform cannot create for itself

Terraform keeps its state in a storage account, so that must exist first. Register the Azure services too.

```bash
az provider register -n Microsoft.CognitiveServices
az provider register -n Microsoft.KeyVault
az group create --name rg-rag-dev --location southindia
az storage account create --name ragtf57o80 --resource-group rg-rag-dev \
  --location southindia --sku Standard_LRS --min-tls-version TLS1_2 \
  --allow-blob-public-access false
az storage container create --name tfstate --account-name ragtf57o80 --auth-mode login
```

Check both providers show `Registered` before step 5:

```bash
az provider show -n Microsoft.CognitiveServices --query registrationState -o tsv
az provider show -n Microsoft.KeyVault --query registrationState -o tsv
```

Storage account names are globally unique. If `ragtf57o80` is taken, pick another and update
`infra/backend.tf` and `infra/qdrant.tf`.

## 2. Create `infra/terraform.tfvars`

It is git-ignored.

```
subscription_id = "<subscription-id>"
ai_account_name = "<globally-unique-lowercase-name>"
```

## 3. Initialise Terraform and create the registry first

The API image must exist before the API app can start, so the registry goes first.

```bash
cd infra
terraform init -reconfigure
terraform apply -target=azurerm_container_registry.acr
cd ..
```

## 4. Build the API and chat UI images

```bash
az acr build --registry ragacr57o80 --image rag-api:v1 ./backend
az acr build --registry ragacr57o80 --image rag-chat-ui:v1 ./chat-ui
```

If ACR Tasks is blocked on your subscription, build locally instead. Use `--platform linux/amd64` on
an Apple Silicon Mac, because Container Apps cannot run arm64 images.

```bash
az acr login --name ragacr57o80
docker buildx build --platform linux/amd64 -t ragacr57o80.azurecr.io/rag-api:v1 --push ./backend
docker buildx build --platform linux/amd64 -t ragacr57o80.azurecr.io/rag-chat-ui:v1 --push ./chat-ui
```

## 5. Deploy everything

```bash
cd infra
terraform plan -out tfplan
terraform apply tfplan
cd ..
```

Expect about 32 resources and 10 to 20 minutes. Postgres and Langfuse's first start are the slow parts.

If the apply fails with a network error, check whether Azure created the resource anyway. If it did,
`terraform import` it and apply again.

## 6. Load the data

```bash
infra/scripts/seed_deployed.sh prompts      # Langfuse prompts, required for chats
infra/scripts/seed_deployed.sh ingest       # AAPL, MSFT, NFLX, GOOG into Qdrant
```

Ingestion runs in the background and takes several minutes. Run it once: re-running a ticker adds its
chunks a second time. Add a ticker after `ingest` (for example `ingest MSFT`) to do one only.

## 7. Open the chat UI

Open `terraform output chat_ui_url`. The UI is built without defaults, so it shows a setup form. Enter:

- **Deployment URL:** `terraform output -raw api_url`
- **Assistant / Graph ID:** `investor_advisor`
- **API key:** `terraform output -raw service_api_key`

The key is stored in the browser only. Anyone you give it to can use the API, so share it deliberately.

## 8. Verify

```bash
cd infra
API=$(terraform output -raw api_url)
curl -H "Authorization: Bearer $(terraform output -raw service_api_key)" $API/health
```

Then ask the chat UI "how is apple's risk factor?".

## Later changes

- **Backend or UI code change:** build a new tag with
  `az acr build --registry ragacr57o80 --image rag-api:v2 ./backend` (or `rag-chat-ui` from `./chat-ui`), then run
  `terraform apply -var api_image_tag=v2` (or `-var chat_ui_image_tag=v2`) from `infra/`. A new tag is what makes Azure start a new revision.
- **Infrastructure change:** edit the `.tf` files and run `terraform apply`.
- **Pause spending:** `az postgres flexible-server stop --name rag-pg-57o80 --resource-group rg-rag-dev`.
  Azure restarts it automatically after 7 days.
- **Delete everything:** `terraform destroy` from `infra/`. It leaves the resource group and the state
  storage in place. Key Vault purge protection keeps the vault's name reserved for 7 days.

## Known issues and decisions

- The chat model is `gpt-5.4-mini`. `gpt-5.4` had no `DataZoneStandard` quota on this subscription.
  Check `az cognitiveservices usage list -l southindia` before changing models.
- The API's health probes are TCP, because `/health` requires a bearer token.
- The chat UI scales to zero (it is stateless), so its first request after idle takes a few seconds.
- Deleting the resource group also deletes the Terraform state storage, and leaves the Key Vault (purge
  protection, 7 days) and AI account (48 days) soft-deleted with their names reserved. Before redeploying,
  pick new names for them or run `az cognitiveservices account purge`. Check
  `az keyvault list-deleted` and `az cognitiveservices account list-deleted`.
- `uv run` fails inside `backend/` (the project is not an installable package), so the seed script
  calls `backend/.venv/bin/python` directly.
- Python may report certificate errors on networks that intercept TLS. Use `curl`, which uses the
  system certificates, and do not turn verification off.
- Secrets live in Terraform state and Container Apps secrets. Read them with
  `terraform output -raw <name>`; never paste them into files or commands.
