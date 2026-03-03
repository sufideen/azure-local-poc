# =============================================================================
# Azure Local POC – Terraform Main Configuration
# Provisions all Azure cloud-side resources for the clinical POC
# =============================================================================

locals {
  common_tags = {
    Environment = var.environment
    Department  = var.department
    ManagedBy   = "Terraform"
    Project     = "AzureLocalPOC"
    Compliance  = "NHS"
    CreatedDate = "2026-03-03"
  }
}

# ── Resource Group ─────────────────────────────────────────────────────────────
resource "azurerm_resource_group" "poc" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.common_tags
}

# ── Arc Service Principal ──────────────────────────────────────────────────────
module "arc" {
  source = "./modules/arc"

  resource_group_id   = azurerm_resource_group.poc.id
  subscription_id     = var.subscription_id
  sp_display_name     = var.arc_sp_display_name
  common_tags         = local.common_tags
}

# ── Log Analytics Workspace ────────────────────────────────────────────────────
module "monitoring" {
  source = "./modules/monitoring"

  resource_group_name = azurerm_resource_group.poc.name
  location            = var.location
  workspace_name      = var.law_workspace_name
  retention_days      = var.law_retention_days
  alert_email         = var.alert_email
  subscription_id     = var.subscription_id
  resource_group_id   = azurerm_resource_group.poc.id
  arc_host_name       = var.arc_host_machine_name
  arc_vm_name         = var.arc_vm_machine_name
  common_tags         = local.common_tags

  depends_on = [azurerm_resource_group.poc]
}

# ── Recovery Services Vault & Backup ──────────────────────────────────────────
module "backup" {
  source = "./modules/backup"

  resource_group_name            = azurerm_resource_group.poc.name
  location                       = var.location
  rsv_name                       = var.rsv_name
  backup_retention_daily_count   = var.backup_retention_daily_count
  backup_retention_weekly_count  = var.backup_retention_weekly_count
  backup_retention_monthly_count = var.backup_retention_monthly_count
  common_tags                    = local.common_tags

  depends_on = [azurerm_resource_group.poc]
}

# ── Key Vault (for secrets management) ────────────────────────────────────────
resource "random_string" "kv_suffix" {
  length  = 4
  special = false
  upper   = false
}

resource "azurerm_key_vault" "poc" {
  name                        = "${var.key_vault_name_prefix}-${random_string.kv_suffix.result}"
  location                    = azurerm_resource_group.poc.location
  resource_group_name         = azurerm_resource_group.poc.name
  tenant_id                   = var.tenant_id
  sku_name                    = "standard"
  soft_delete_retention_days  = 90
  purge_protection_enabled    = true
  enable_rbac_authorization   = true

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
    ip_rules       = []  # Add your deployment machine IP here in production
  }

  tags = local.common_tags
}

# Grant Arc SP access to Key Vault secrets
resource "azurerm_role_assignment" "arc_kv_reader" {
  scope                = azurerm_key_vault.poc.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = module.arc.arc_sp_object_id
}

# ── Azure Policy Assignments ───────────────────────────────────────────────────
# Guest Configuration prerequisites
resource "azurerm_resource_group_policy_assignment" "gc_prereqs" {
  name                 = "arc-gc-prereqs"
  resource_group_id    = azurerm_resource_group.poc.id
  policy_definition_id = "/providers/Microsoft.Authorization/policyDefinitions/12794019-7a00-42cf-95c2-882eed337cc8"
  location             = var.location
  identity { type = "SystemAssigned" }
}

# Windows security baseline via Guest Configuration
resource "azurerm_resource_group_policy_assignment" "windows_baseline" {
  name                 = "arc-windows-security-baseline"
  resource_group_id    = azurerm_resource_group.poc.id
  policy_definition_id = "/providers/Microsoft.Authorization/policyDefinitions/72650e9f-97bc-4b2a-ab5f-9781a9fcecbc"
  location             = var.location
  identity { type = "SystemAssigned" }
}

# Audit BitLocker on Windows machines
resource "azurerm_resource_group_policy_assignment" "bitlocker_audit" {
  name                 = "arc-bitlocker-audit"
  resource_group_id    = azurerm_resource_group.poc.id
  policy_definition_id = "/providers/Microsoft.Authorization/policyDefinitions/013e242c-8828-4970-87b3-ab247555486d"
  location             = var.location
  identity { type = "SystemAssigned" }
}
