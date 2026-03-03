# =============================================================================
# Module: backup
# Recovery Services Vault and backup policies for clinical POC
# =============================================================================

variable "resource_group_name"            { type = string }
variable "location"                        { type = string }
variable "rsv_name"                        { type = string }
variable "backup_retention_daily_count"    { type = number }
variable "backup_retention_weekly_count"   { type = number }
variable "backup_retention_monthly_count"  { type = number }
variable "common_tags"                     { type = map(string) }

# ── Recovery Services Vault ────────────────────────────────────────────────────
resource "azurerm_recovery_services_vault" "poc" {
  name                = var.rsv_name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "Standard"

  soft_delete_enabled = true

  # Geo-redundant storage for production resilience
  storage_mode_type = "GeoRedundant"

  identity {
    type = "SystemAssigned"
  }

  tags = var.common_tags
}

# ── VM Backup Policy (Enhanced – supports hourly backups) ──────────────────────
resource "azurerm_backup_policy_vm" "clinical_vms" {
  name                = "policy-clinical-vms"
  resource_group_name = var.resource_group_name
  recovery_vault_name = azurerm_recovery_services_vault.poc.name
  policy_type         = "V2"   # Enhanced policy

  timezone = "UTC"

  backup {
    frequency = "Daily"
    time      = "02:00"
  }

  retention_daily {
    count = var.backup_retention_daily_count
  }

  retention_weekly {
    count    = var.backup_retention_weekly_count
    weekdays = ["Sunday"]
  }

  retention_monthly {
    count    = var.backup_retention_monthly_count
    weekdays = ["Sunday"]
    weeks    = ["First"]
  }

  retention_yearly {
    count    = 1
    weekdays = ["Sunday"]
    weeks    = ["First"]
    months   = ["January"]
  }
}

# ── File Share Backup Policy (for any Azure File shares) ──────────────────────
resource "azurerm_backup_policy_file_share" "clinical_files" {
  name                = "policy-clinical-fileshares"
  resource_group_name = var.resource_group_name
  recovery_vault_name = azurerm_recovery_services_vault.poc.name

  timezone = "UTC"

  backup {
    frequency = "Daily"
    time      = "03:00"
  }

  retention_daily {
    count = 30
  }

  retention_weekly {
    count    = 8
    weekdays = ["Sunday"]
  }
}

# ── Diagnostic Settings on RSV ────────────────────────────────────────────────
# (Requires monitoring module workspace ID – passed via root module)
# Uncomment and wire up law_workspace_id if needed:
# resource "azurerm_monitor_diagnostic_setting" "rsv_diag" {
#   name               = "rsv-diag-to-law"
#   target_resource_id = azurerm_recovery_services_vault.poc.id
#   log_analytics_workspace_id = var.law_workspace_id
#   log { category = "AzureBackupReport" enabled = true }
#   log { category = "CoreAzureBackup"   enabled = true }
#   log { category = "AddonAzureBackupJobs" enabled = true }
# }

# ── Outputs ───────────────────────────────────────────────────────────────────
output "rsv_id"              { value = azurerm_recovery_services_vault.poc.id }
output "rsv_name"            { value = azurerm_recovery_services_vault.poc.name }
output "vm_backup_policy_id" { value = azurerm_backup_policy_vm.clinical_vms.id }
