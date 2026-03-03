output "resource_group_name" {
  description = "Resource group name"
  value       = azurerm_resource_group.poc.name
}

output "arc_sp_app_id" {
  description = "Arc Service Principal Application ID (use in azcmagent connect)"
  value       = module.arc.arc_sp_app_id
  sensitive   = true
}

output "arc_sp_object_id" {
  description = "Arc Service Principal Object ID"
  value       = module.arc.arc_sp_object_id
}

output "law_workspace_id" {
  description = "Log Analytics Workspace Customer ID (for agent configuration)"
  value       = module.monitoring.workspace_id
}

output "law_workspace_resource_id" {
  description = "Log Analytics Workspace Resource ID"
  value       = module.monitoring.workspace_resource_id
}

output "rsv_id" {
  description = "Recovery Services Vault Resource ID"
  value       = module.backup.rsv_id
}

output "key_vault_name" {
  description = "Key Vault name"
  value       = azurerm_key_vault.poc.name
}

output "key_vault_uri" {
  description = "Key Vault URI"
  value       = azurerm_key_vault.poc.vault_uri
}

output "azcmagent_connect_command_host" {
  description = "Command to connect Hyper-V host to Azure Arc"
  value       = <<-EOT
    azcmagent connect `
      --service-principal-id     "<arc_sp_app_id – see arc_sp_app_id output>" `
      --service-principal-secret "<generated secret>" `
      --resource-group           "${azurerm_resource_group.poc.name}" `
      --tenant-id                "${var.tenant_id}" `
      --location                 "${var.location}" `
      --subscription-id          "${var.subscription_id}" `
      --resource-name            "az-local-host01"
  EOT
}
