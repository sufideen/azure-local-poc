# =============================================================================
# Module: monitoring
# Log Analytics Workspace, alerts, and diagnostic settings
# =============================================================================

variable "resource_group_name" { type = string }
variable "location"            { type = string }
variable "workspace_name"      { type = string }
variable "retention_days"      { type = number }
variable "alert_email"         { type = string }
variable "subscription_id"     { type = string }
variable "resource_group_id"   { type = string }
variable "arc_host_name"       { type = string }
variable "arc_vm_name"         { type = string }
variable "common_tags"         { type = map(string) }

# ── Log Analytics Workspace ────────────────────────────────────────────────────
resource "azurerm_log_analytics_workspace" "poc" {
  name                = var.workspace_name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_days
  tags                = var.common_tags
}

# ── Solutions ──────────────────────────────────────────────────────────────────
resource "azurerm_log_analytics_solution" "security" {
  solution_name         = "Security"
  location              = var.location
  resource_group_name   = var.resource_group_name
  workspace_resource_id = azurerm_log_analytics_workspace.poc.id
  workspace_name        = azurerm_log_analytics_workspace.poc.name

  plan {
    publisher = "Microsoft"
    product   = "OMSGallery/Security"
  }

  tags = var.common_tags
}

resource "azurerm_log_analytics_solution" "security_center_free" {
  solution_name         = "SecurityCenterFree"
  location              = var.location
  resource_group_name   = var.resource_group_name
  workspace_resource_id = azurerm_log_analytics_workspace.poc.id
  workspace_name        = azurerm_log_analytics_workspace.poc.name

  plan {
    publisher = "Microsoft"
    product   = "OMSGallery/SecurityCenterFree"
  }

  tags = var.common_tags
}

# ── Saved Queries (KQL) ────────────────────────────────────────────────────────
resource "azurerm_log_analytics_saved_search" "failed_logins" {
  name                       = "ClinicalFailedLogins"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.poc.id
  category                   = "Clinical Security"
  display_name               = "Failed Login Attempts (Last 24h)"
  query                      = <<-KQL
    SecurityEvent
    | where TimeGenerated > ago(24h)
    | where EventID == 4625
    | summarize FailedAttempts = count() by Account, Computer, bin(TimeGenerated, 1h)
    | where FailedAttempts > 5
    | order by FailedAttempts desc
  KQL
}

resource "azurerm_log_analytics_saved_search" "arc_heartbeat" {
  name                       = "ArcAgentHeartbeat"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.poc.id
  category                   = "Clinical Infrastructure"
  display_name               = "Azure Arc Agent Heartbeat (Last 1h)"
  query                      = <<-KQL
    Heartbeat
    | where TimeGenerated > ago(1h)
    | where Computer contains "CLINICAL" or Computer contains "az-local"
    | summarize LastHeartbeat = max(TimeGenerated) by Computer, OSType
    | extend MinutesSinceHeartbeat = datetime_diff('minute', now(), LastHeartbeat)
    | order by MinutesSinceHeartbeat desc
  KQL
}

resource "azurerm_log_analytics_saved_search" "asa_syslog" {
  name                       = "ASASyslogEvents"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.poc.id
  category                   = "Clinical Network"
  display_name               = "Cisco ASA Syslog Events"
  query                      = <<-KQL
    Syslog
    | where TimeGenerated > ago(1h)
    | where ProcessName contains "ASA"
    | where SeverityLevel in ("warning", "err", "crit", "alert", "emerg")
    | project TimeGenerated, HostName, SeverityLevel, SyslogMessage
    | order by TimeGenerated desc
  KQL
}

# ── Action Group ──────────────────────────────────────────────────────────────
resource "azurerm_monitor_action_group" "clinical_alerts" {
  name                = "ag-clinical-alerts"
  resource_group_name = var.resource_group_name
  short_name          = "ClinAlert"
  tags                = var.common_tags

  email_receiver {
    name                    = "clinical-it-team"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }
}

# ── Metric Alerts ─────────────────────────────────────────────────────────────
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "arc_agent_silent" {
  name                = "alert-arc-agent-silent"
  location            = var.location
  resource_group_name = var.resource_group_name
  description         = "Arc agent has not sent a heartbeat in 15 minutes"
  severity            = 1
  enabled             = true
  tags                = var.common_tags

  window_duration      = "PT15M"
  evaluation_frequency = "PT5M"
  scope_resource_ids   = [azurerm_log_analytics_workspace.poc.id]

  criteria {
    query                   = <<-KQL
      Heartbeat
      | where Computer in ("az-local-host01", "clinical-vm-01")
      | summarize LastBeat = max(TimeGenerated) by Computer
      | where LastBeat < ago(15m)
    KQL
    operator                = "GreaterThan"
    threshold               = 0
    time_aggregation_method = "Count"
  }

  action {
    action_groups = [azurerm_monitor_action_group.clinical_alerts.id]
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "failed_logins_alert" {
  name                = "alert-clinical-failed-logins"
  location            = var.location
  resource_group_name = var.resource_group_name
  description         = "More than 10 failed logins in 10 minutes on clinical systems"
  severity            = 2
  enabled             = true
  tags                = var.common_tags

  window_duration      = "PT10M"
  evaluation_frequency = "PT5M"
  scope_resource_ids   = [azurerm_log_analytics_workspace.poc.id]

  criteria {
    query                   = <<-KQL
      SecurityEvent
      | where EventID == 4625
      | where Computer contains "CLINICAL"
      | count
    KQL
    operator                = "GreaterThan"
    threshold               = 10
    time_aggregation_method = "Count"
  }

  action {
    action_groups = [azurerm_monitor_action_group.clinical_alerts.id]
  }
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "workspace_id"          { value = azurerm_log_analytics_workspace.poc.workspace_id }
output "workspace_resource_id" { value = azurerm_log_analytics_workspace.poc.id }
output "action_group_id"       { value = azurerm_monitor_action_group.clinical_alerts.id }
