# =============================================================================
# Module: arc
# Creates the Service Principal and RBAC roles for Azure Arc onboarding
# =============================================================================

terraform {
  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.47"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
  }
}

variable "resource_group_id"   { type = string }
variable "subscription_id"     { type = string }
variable "sp_display_name"     { type = string }
variable "common_tags"         { type = map(string) }

# ── Entra ID Application ───────────────────────────────────────────────────────
resource "azuread_application" "arc" {
  display_name = var.sp_display_name
}

resource "azuread_service_principal" "arc" {
  client_id = azuread_application.arc.client_id
}

# Rotate secret every 12 months
resource "time_rotating" "arc_sp_secret_rotation" {
  rotation_months = 12
}

resource "azuread_application_password" "arc" {
  application_id = azuread_application.arc.id
  display_name   = "arc-onboarding-secret"
  rotate_when_changed = {
    rotation = time_rotating.arc_sp_secret_rotation.id
  }
}

# ── RBAC Assignments ──────────────────────────────────────────────────────────
# Azure Connected Machine Onboarding role – needed for azcmagent connect
resource "azurerm_role_assignment" "arc_onboarding" {
  scope                = var.resource_group_id
  role_definition_name = "Azure Connected Machine Onboarding"
  principal_id         = azuread_service_principal.arc.object_id
}

# Connected Machine Resource Administrator – for extensions
resource "azurerm_role_assignment" "arc_resource_admin" {
  scope                = var.resource_group_id
  role_definition_name = "Azure Connected Machine Resource Administrator"
  principal_id         = azuread_service_principal.arc.object_id
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "arc_sp_app_id"     { value = azuread_application.arc.client_id }
output "arc_sp_object_id"  { value = azuread_service_principal.arc.object_id }
output "arc_sp_secret"     {
  value     = azuread_application_password.arc.value
  sensitive = true
}
