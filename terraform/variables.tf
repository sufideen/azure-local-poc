variable "subscription_id" {
  description = "Azure Subscription ID"
  type        = string
}

variable "tenant_id" {
  description = "Microsoft Entra ID Tenant ID"
  type        = string
}

variable "location" {
  description = "Azure region for all resources"
  type        = string
  default     = "uksouth"
}

variable "resource_group_name" {
  description = "Resource group for POC resources"
  type        = string
  default     = "rg-clinical-poc"
}

variable "environment" {
  description = "Environment tag value"
  type        = string
  default     = "POC"
}

variable "department" {
  description = "Department tag value"
  type        = string
  default     = "Clinical"
}

# ── Arc Service Principal ──────────────────────────────────────────────────────
variable "arc_sp_display_name" {
  description = "Display name for the Arc onboarding service principal"
  type        = string
  default     = "sp-arc-onboarding-clinical-poc"
}

# ── Log Analytics ─────────────────────────────────────────────────────────────
variable "law_workspace_name" {
  description = "Log Analytics Workspace name"
  type        = string
  default     = "law-clinical-poc"
}

variable "law_retention_days" {
  description = "Log Analytics retention in days"
  type        = number
  default     = 90
}

# ── Recovery Services Vault ───────────────────────────────────────────────────
variable "rsv_name" {
  description = "Recovery Services Vault name"
  type        = string
  default     = "rsv-clinical-poc"
}

variable "backup_retention_daily_count" {
  description = "Number of daily backup recovery points to keep"
  type        = number
  default     = 30
}

variable "backup_retention_weekly_count" {
  description = "Number of weekly backup recovery points to keep"
  type        = number
  default     = 12
}

variable "backup_retention_monthly_count" {
  description = "Number of monthly backup recovery points to keep"
  type        = number
  default     = 12
}

# ── Key Vault ─────────────────────────────────────────────────────────────────
variable "key_vault_name_prefix" {
  description = "Prefix for Key Vault name (must be globally unique)"
  type        = string
  default     = "kv-clinical-poc"
}

# ── Arc Machine Names ─────────────────────────────────────────────────────────
variable "arc_host_machine_name" {
  description = "Name of Arc-connected Hyper-V host machine"
  type        = string
  default     = "az-local-host01"
}

variable "arc_vm_machine_name" {
  description = "Name of Arc-connected Clinical VM"
  type        = string
  default     = "clinical-vm-01"
}

# ── Alert email ───────────────────────────────────────────────────────────────
variable "alert_email" {
  description = "Email address for monitoring alerts"
  type        = string
  default     = "clinical-it@corp.clinical.local"
}
