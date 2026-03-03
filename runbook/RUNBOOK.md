# Azure Local POC – Detailed Deployment Runbook

**Version:** 1.0 | **Date:** 2026-03-03 | **Classification:** Internal / Restricted

---

## Step 1 – Prepare Dell Server (BIOS, Secure Boot, Firmware)

### Prerequisites
- Physical access or iDRAC 9 remote access
- Dell Repository Manager (DRM) or DSU installed
- Maintenance window confirmed

### 1.1 BIOS Configuration (via iDRAC Lifecycle Controller)

```
F2 → System Setup → BIOS Settings:

Boot Mode:           UEFI
Secure Boot:         Enabled (Deploy Mode → Standard)
Intel VT-x:         Enabled
Intel VT-d:         Enabled (IOMMU)
SR-IOV:             Enabled
Hyper-Threading:    Enabled
C-States:           Enabled (C1E, C3, C6)
TPM Security:       Enabled (TPM 2.0)
TPM Advanced:       Activated, Enabled
NX/XD Bit:         Enabled
Memory Mapped I/O: Above 4GB Enabled
```

### 1.2 Firmware Update via Dell DSU

```bash
# On the Dell server OS or via iDRAC OS Deploy
curl -O https://dl.dell.com/FOLDER/dsu.sh   # Use your Dell DSU URL
chmod +x dsu.sh
./dsu --non-interactive --apply-upgrades

# iDRAC REST API firmware update
curl -k -X POST \
  "https://<idrac-ip>/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate" \
  -H "Content-Type: application/json" \
  -u admin:<password> \
  -d '{"ImageURI":"http://<repo-server>/firmware/BIOS_latest.exe","TransferProtocol":"HTTP"}'
```

### 1.3 Validation Checklist
- [ ] TPM 2.0 visible and Active in BIOS
- [ ] Secure Boot mode = Deployed (Standard Keys)
- [ ] All firmware components at latest supported version
- [ ] iDRAC firmware ≥ 6.10.30

---

## Step 2 – Install Windows Server 2022 Azure Edition

### 2.1 Installation Notes

Windows Server 2022 Azure Edition requires UEFI boot and is available via:
- Azure Marketplace (for Azure Local / Stack HCI scenarios)
- VLSC ISO: `WindowsServer2022AzureEdition_x64.iso`

```
Installation options:
  Edition:         Windows Server 2022 Datacenter: Azure Edition (Desktop Experience)
  Partition:       C:\ = 200 GB OS (NTFS, GPT)
                   D:\ = Remaining for VM storage
  Time Zone:       UTC (convert for local display only)
  Computer Name:   AZ-LOCAL-HOST01
  Domain:          corp.clinical.local
```

### 2.2 Post-Install Configuration

```powershell
# Set static IP (Management NIC)
New-NetIPAddress -InterfaceAlias "Management" -IPAddress 192.168.20.10 `
  -PrefixLength 24 -DefaultGateway 192.168.20.1
Set-DnsClientServerAddress -InterfaceAlias "Management" -ServerAddresses 192.168.20.5,8.8.8.8

# Join domain
Add-Computer -DomainName "corp.clinical.local" -Credential (Get-Credential) -Restart

# Enable WinRM for Ansible
Enable-PSRemoting -Force
winrm quickconfig -q
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $false

# Windows Update
Install-Module PSWindowsUpdate -Force
Get-WUInstall -AcceptAll -AutoReboot
```

### 2.3 Hotfix Rollup Requirement
Ensure KB5031364 (or later cumulative update) is installed before Azure Arc registration.

---

## Step 3 – Enable Hyper-V, BitLocker, Core Isolation

### 3.1 Enable Hyper-V and Required Features

```powershell
# Install Hyper-V and management tools
Install-WindowsFeature -Name Hyper-V, Hyper-V-Tools, Hyper-V-PowerShell, `
  RSAT-Hyper-V-Tools -IncludeManagementTools -Restart

# Create Virtual Switch (External – bound to Physical NIC for VLAN 10)
New-VMSwitch -Name "Clinical-vSwitch" -NetAdapterName "NIC-Clinical" `
  -AllowManagementOS $false -EnableEmbeddedTeaming $true

# Create Internal switch for VM-to-VM (VLAN 20 management traffic)
New-VMSwitch -Name "Mgmt-vSwitch" -SwitchType Internal

# VLAN tagging on host vNIC
Set-VMNetworkAdapterVlan -VMName * -VlanId 10 -Access -VMNetworkAdapterName "Clinical"
```

### 3.2 BitLocker Configuration

```powershell
# Enable BitLocker on OS drive with TPM + PIN protector
$SecurePin = Read-Host "Enter BitLocker PIN" -AsSecureString
Add-BitLockerKeyProtector -MountPoint "C:" -TpmAndPinProtector -Pin $SecurePin

Enable-BitLocker -MountPoint "C:" -EncryptionMethod XtsAes256 `
  -TpmAndPinProtector -SkipHardwareTest

# Back up recovery key to AD
Backup-BitLockerKeyProtector -MountPoint "C:" `
  -KeyProtectorId (Get-BitLockerVolume C:).KeyProtector[0].KeyProtectorId

# Enable BitLocker on Data drive
Enable-BitLocker -MountPoint "D:" -EncryptionMethod XtsAes256 `
  -RecoveryPasswordProtector

# Verify
Get-BitLockerVolume | Select MountPoint,EncryptionMethod,VolumeStatus,ProtectionStatus
```

### 3.3 Core Isolation / Memory Integrity (HVCI)

```powershell
# Enable via registry (requires reboot)
$CIpath = "HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity"
New-Item -Path $CIpath -Force | Out-Null
Set-ItemProperty -Path $CIpath -Name "Enabled" -Value 1 -Type DWord

# Enable Credential Guard
$CGpath = "HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard"
Set-ItemProperty -Path $CGpath -Name "EnableVirtualizationBasedSecurity" -Value 1
Set-ItemProperty -Path $CGpath -Name "RequirePlatformSecurityFeatures" -Value 3  # TPM + Secure Boot
Set-ItemProperty -Path $CGpath -Name "LsaCfgFlags" -Value 1  # Credential Guard enabled

# Enable Windows Defender Application Control baseline
New-CIPolicy -Level Publisher -Fallback Hash -FilePath C:\WDAC\BaselinePolicy.xml `
  -UserPEs -ScanPath C:\Windows\System32

# Restart to apply
Restart-Computer -Force
```

---

## Step 4 – Register Host with Azure Arc (azcmagent connect)

### 4.1 Prerequisites in Azure

```bash
# Azure CLI – create resource group and enable Arc providers
az login --use-device-code
az account set --subscription "<subscription-id>"

az group create --name rg-clinical-poc --location uksouth

# Register required resource providers
az provider register --namespace Microsoft.HybridCompute
az provider register --namespace Microsoft.GuestConfiguration
az provider register --namespace Microsoft.HybridConnectivity
az provider register --namespace Microsoft.AzureArcData

# Create Service Principal for Arc onboarding
az ad sp create-for-rbac --name "sp-arc-onboarding" \
  --role "Azure Connected Machine Onboarding" \
  --scopes "/subscriptions/<subscription-id>/resourceGroups/rg-clinical-poc"
# Save: appId, password, tenant
```

### 4.2 Install and Connect azcmagent (on Windows Server host)

```powershell
# Download and install Azure Connected Machine Agent
Invoke-WebRequest -Uri "https://aka.ms/azcmagent-windows" `
  -OutFile "$env:TEMP\install_windows_azcmagent.ps1"

& "$env:TEMP\install_windows_azcmagent.ps1"

# Connect to Azure Arc
azcmagent connect `
  --service-principal-id     "<appId>" `
  --service-principal-secret "<password>" `
  --resource-group           "rg-clinical-poc" `
  --tenant-id                "<tenant-id>" `
  --location                 "uksouth" `
  --subscription-id          "<subscription-id>" `
  --resource-name            "az-local-host01" `
  --tags                     "Environment=POC,Department=Clinical,Compliance=NHS"

# Verify connection
azcmagent show
# Expected: Status = Connected
```

### 4.3 Assign Azure Policies to Arc Machine

```bash
# Assign Guest Configuration prerequisites
az policy assignment create \
  --name "arc-gc-prereqs" \
  --policy "12794019-7a00-42cf-95c2-882eed337cc8" \
  --scope "/subscriptions/<sub-id>/resourceGroups/rg-clinical-poc" \
  --location uksouth \
  --assign-identity

# Assign Windows security baseline
az policy assignment create \
  --name "arc-windows-baseline" \
  --policy "72650e9f-97bc-4b2a-ab5f-9781a9fcecbc" \
  --scope "/subscriptions/<sub-id>/resourceGroups/rg-clinical-poc"
```

---

## Step 5 – Configure Cisco ASA

### 5.1 Environment Details

| Parameter          | Value                          |
|--------------------|-------------------------------|
| ASA Model          | Cisco ASA 5506-X               |
| ASA Version        | 9.18(x) recommended            |
| ASDM Version       | 7.18(x)                        |
| Outside Interface  | GigabitEthernet1/1 – 203.0.113.10/24 |
| Inside Interface   | GigabitEthernet1/2 – 192.168.10.1/24 (VLAN 10) |
| DMZ Interface      | GigabitEthernet1/3 – 192.168.20.1/24 (VLAN 20) |
| AnyConnect Pool    | 192.168.100.0/24               |
| SAML IdP           | Microsoft Entra ID             |

### 5.2 Full ASA Configuration

```
! ============================================================
! Cisco ASA - Azure Local POC Clinical Environment
! Version: 9.18 | Date: 2026-03-03
! ============================================================

! --- Global Settings ---
hostname ASA-CLINICAL-POC
domain-name corp.clinical.local
enable password <ENCRYPTED> pbkdf2
names
dns server-group DefaultDNS
 name-server 8.8.8.8
 name-server 8.8.4.4

clock timezone UTC 0
clock summer-time BST recurring last Sun Mar 1:00 last Sun Oct 1:00

! --- Interface Configuration ---
interface GigabitEthernet1/1
 description OUTSIDE-INTERNET
 nameif outside
 security-level 0
 ip address 203.0.113.10 255.255.255.0
 no shutdown

interface GigabitEthernet1/2
 description INSIDE-CLINICAL-VLAN10
 nameif inside
 security-level 100
 ip address 192.168.10.1 255.255.255.0
 no shutdown

interface GigabitEthernet1/3
 description DMZ-MANAGEMENT-VLAN20
 nameif dmz
 security-level 50
 ip address 192.168.20.1 255.255.255.0
 no shutdown

interface GigabitEthernet1/4
 description UNUSED
 shutdown
 no nameif

! --- VLAN Sub-interfaces (if using trunk from switch) ---
! Uncomment if using 802.1Q trunking instead of dedicated ports
! interface GigabitEthernet1/2.10
!  vlan 10
!  nameif inside
!  security-level 100
!  ip address 192.168.10.1 255.255.255.0
! interface GigabitEthernet1/2.20
!  vlan 20
!  nameif dmz
!  security-level 50
!  ip address 192.168.20.1 255.255.255.0

! --- Routing ---
route outside 0.0.0.0 0.0.0.0 203.0.113.1 1
route inside 192.168.10.0 255.255.255.0 192.168.10.1 1
route dmz 192.168.20.0 255.255.255.0 192.168.20.1 1

! --- Object Definitions ---
object network OBJ-INSIDE-NET
 subnet 192.168.10.0 255.255.255.0
 description Clinical VLAN 10

object network OBJ-DMZ-NET
 subnet 192.168.20.0 255.255.255.0
 description Management VLAN 20

object network OBJ-VPN-POOL
 subnet 192.168.100.0 255.255.255.0
 description AnyConnect VPN Client Pool

object network OBJ-AZURE-PUBLIC-IP
 host 203.0.113.10
 description ASA Outside Public IP

! --- NAT Configuration ---
! PAT: Clinical clients out to internet
nat (inside,outside) after-auto source dynamic OBJ-INSIDE-NET interface
! PAT: DMZ/Management out to internet
nat (dmz,outside) after-auto source dynamic OBJ-DMZ-NET interface
! VPN clients: No NAT (direct routing via split tunnel)
nat (outside,inside) source static OBJ-VPN-POOL OBJ-VPN-POOL \
  destination static OBJ-INSIDE-NET OBJ-INSIDE-NET no-proxy-arp route-lookup

! --- Access Control Lists ---
! Inbound on Outside (only VPN and management)
access-list ACL-OUTSIDE-IN extended permit tcp any host 203.0.113.10 eq 443  ! AnyConnect HTTPS
access-list ACL-OUTSIDE-IN extended permit udp any host 203.0.113.10 eq 443  ! DTLS
access-list ACL-OUTSIDE-IN extended deny ip any any log

! Clinical VLAN (inside) egress rules
access-list ACL-INSIDE-OUT extended permit tcp 192.168.10.0 255.255.255.0 any eq 443  ! HTTPS
access-list ACL-INSIDE-OUT extended permit tcp 192.168.10.0 255.255.255.0 any eq 80   ! HTTP
access-list ACL-INSIDE-OUT extended permit udp 192.168.10.0 255.255.255.0 any eq 53   ! DNS
access-list ACL-INSIDE-OUT extended permit tcp 192.168.10.0 255.255.255.0 any eq 53   ! DNS/TCP
access-list ACL-INSIDE-OUT extended permit tcp 192.168.10.0 255.255.255.0 any eq 123  ! NTP
access-list ACL-INSIDE-OUT extended deny ip 192.168.10.0 255.255.255.0 192.168.20.0 255.255.255.0 log  ! No VLAN 10→20
access-list ACL-INSIDE-OUT extended deny ip any any log

! VPN clients to Clinical VLAN
access-list ACL-VPN-TO-INSIDE extended permit tcp 192.168.100.0 255.255.255.0 192.168.10.0 255.255.255.0 eq 3389  ! RDP
access-list ACL-VPN-TO-INSIDE extended permit tcp 192.168.100.0 255.255.255.0 192.168.10.0 255.255.255.0 eq 443
access-list ACL-VPN-TO-INSIDE extended permit icmp 192.168.100.0 255.255.255.0 192.168.10.0 255.255.255.0
access-list ACL-VPN-TO-INSIDE extended deny ip any any log

! Management VLAN to Clinical (restricted)
access-list ACL-DMZ-TO-INSIDE extended permit tcp 192.168.20.0 255.255.255.0 192.168.10.0 255.255.255.0 eq 3389
access-list ACL-DMZ-TO-INSIDE extended permit tcp 192.168.20.0 255.255.255.0 192.168.10.0 255.255.255.0 eq 5985
access-list ACL-DMZ-TO-INSIDE extended permit icmp 192.168.20.0 255.255.255.0 192.168.10.0 255.255.255.0
access-list ACL-DMZ-TO-INSIDE extended deny ip any any log

! Apply ACLs to interfaces
access-group ACL-OUTSIDE-IN  in  interface outside
access-group ACL-INSIDE-OUT  in  interface inside
access-group ACL-DMZ-TO-INSIDE in interface dmz

! --- Crypto: TLS/SSL Policy ---
ssl server-version tlsv1.2
ssl cipher tlsv1.2 high
ssl dh-group group14
ssl trust-point SELF-SIGNED outside

! Generate RSA key and self-signed cert (replace with CA-signed in production)
crypto key generate rsa label ASA-VPN-KEY modulus 4096
crypto ca trustpoint SELF-SIGNED
 enrollment self
 subject-name CN=asa-clinical-poc.corp.clinical.local,OU=IT,O=Clinical,C=GB
 keypair ASA-VPN-KEY
 fqdn asa-clinical-poc.corp.clinical.local
crypto ca enroll SELF-SIGNED noconfirm

! --- IKEv2 / AnyConnect ---
crypto ikev2 enable outside
webvpn
 enable outside
 anyconnect enable
 anyconnect image disk0:/anyconnect-win-<version>-webdeploy-k9.pkg 1
 anyconnect profiles CLINICAL-VPN-PROFILE disk0:/clinical_vpn_profile.xml
 cache disable
 error-recovery disable

! --- SAML Configuration (Microsoft Entra ID) ---
saml idp https://sts.windows.net/<tenant-id>/
 url sign-in  https://login.microsoftonline.com/<tenant-id>/saml2
 url sign-out https://login.microsoftonline.com/<tenant-id>/saml2
 base-url     https://203.0.113.10
 trustpoint idp ENTRA-ID-CERT
 trustpoint sp  SELF-SIGNED
 no signature
 timeout assertion 1800

! Import Entra ID signing certificate:
! crypto ca authenticate ENTRA-ID-CERT
! (Paste federation metadata certificate from Entra ID Enterprise App)

! --- Group Policy ---
group-policy GP-CLINICAL-VPN internal
group-policy GP-CLINICAL-VPN attributes
 vpn-tunnel-protocol ssl-client
 split-tunnel-policy tunnelspecified
 split-tunnel-network-list value ACL-VPN-SPLIT
 dns-server value 192.168.20.5
 wins-server none
 default-domain value corp.clinical.local
 vpn-session-timeout 480
 vpn-idle-timeout 30
 anyconnect profiles value CLINICAL-VPN-PROFILE type user
 webvpn
  anyconnect keep-installer installed
  anyconnect dtls enable
  anyconnect mtu 1400
  anyconnect firewall-rule client-interface public permit ip any any

! Split tunnel ACL (only clinical subnets go through VPN)
access-list ACL-VPN-SPLIT standard permit 192.168.10.0 255.255.255.0
access-list ACL-VPN-SPLIT standard permit 192.168.20.0 255.255.255.0

! --- Tunnel Group (Connection Profile) ---
tunnel-group CLINICAL-SAML type remote-access
tunnel-group CLINICAL-SAML general-attributes
 address-pool VPN-ADDRESS-POOL
 default-group-policy GP-CLINICAL-VPN
 authorization-required
tunnel-group CLINICAL-SAML webvpn-attributes
 authentication saml
 saml identity-provider https://sts.windows.net/<tenant-id>/
 group-alias ClinicalVPN enable
 without-csd

! VPN address pool
ip local pool VPN-ADDRESS-POOL 192.168.100.10-192.168.100.100 mask 255.255.255.0

! --- Syslog ---
logging enable
logging timestamp
logging buffered informational
logging host inside 192.168.20.20 udp/514  ! Log to SIEM/Log Analytics Forwarder
logging trap informational
logging facility 20
logging device-id hostname

! --- SNMP ---
snmp-server community <COMMUNITY-STRING> ro
snmp-server location UK-Clinical-POC-DC1
snmp-server host inside 192.168.20.20 community <COMMUNITY-STRING>

! --- SSH Management ---
ssh 192.168.20.0 255.255.255.0 dmz
ssh 192.168.100.0 255.255.255.0 outside  ! VPN clients
ssh timeout 10
ssh version 2
ssh key-exchange group dh-group14-sha256
aaa authentication ssh console LOCAL
username admin password <ENCRYPTED> privilege 15

! --- NTP ---
ntp server 169.254.169.123  ! Azure NTP
ntp authenticate
ntp trusted-key 1

! --- Threat Detection ---
threat-detection basic-threat
threat-detection statistics
threat-detection statistics tcp-intercept rate-interval 30 burst-rate 400 average-rate 200

! --- Firepower Module (if installed) ---
! access-list REDIRECT_TO_SFR extended permit ip any any
! class-map SFR_MAP
!  match access-list REDIRECT_TO_SFR
! policy-map GLOBAL_POLICY
!  class SFR_MAP
!   sfr fail-open

! --- Save configuration ---
write memory
```

### 5.3 Entra ID SAML App Registration (Portal Steps)

```
Azure Portal → Entra ID → Enterprise Applications → New Application
→ Create your own application → "AnyConnect Clinical VPN" → Non-gallery

Single sign-on → SAML:
  Basic SAML Configuration:
    Identifier (Entity ID):  https://203.0.113.10
    Reply URL (ACS):         https://203.0.113.10/saml/sp/acs?tgname=CLINICAL-SAML
    Sign on URL:             https://203.0.113.10
    Relay State:             (leave blank)
    Logout URL:              https://203.0.113.10/+CSCOT+/saml-auth?type=logout

  Attributes & Claims:
    NameID format:  emailAddress
    Additional:     groups → user.groups  (for authorization)

  SAML Signing Certificate:
    → Download "Certificate (Base64)"
    → Import to ASA: crypto ca authenticate ENTRA-ID-CERT

  Assign users/groups:
    → Add clinical staff Entra ID group to app assignment
```

---

## Step 6 – Deploy Clinical VM on Hyper-V

### 6.1 VM Creation

```powershell
# Define VM parameters
$VMName      = "CLINICAL-VM-01"
$VMPath      = "D:\VMs\$VMName"
$VHDPath     = "$VMPath\$VMName-OS.vhdx"
$ISOPath     = "D:\ISO\WindowsServer2022.iso"
$VLANId      = 10
$VMRam       = 8GB
$VMCPUCount  = 4
$VHDSize     = 127GB

# Create VM directory
New-Item -ItemType Directory -Path $VMPath -Force

# Create VM
New-VM -Name $VMName -Path $VMPath -Generation 2 `
  -MemoryStartupBytes $VMRam -NewVHDPath $VHDPath -NewVHDSizeBytes $VHDSize `
  -SwitchName "Clinical-vSwitch"

# Configure VM
Set-VM -Name $VMName `
  -ProcessorCount $VMCPUCount `
  -DynamicMemory -MemoryMinimumBytes 4GB -MemoryMaximumBytes 16GB `
  -AutomaticStartAction Start -AutomaticStopAction ShutDown `
  -CheckpointType Disabled

# Security: Enable Secure Boot and TPM
Set-VMFirmware -VMName $VMName -EnableSecureBoot On `
  -SecureBootTemplate "MicrosoftWindows"
Enable-VMTPM -VMName $VMName

# Add DVD for OS install
Add-VMDvdDrive -VMName $VMName -Path $ISOPath
$DVDDrive = Get-VMDvdDrive -VMName $VMName
Set-VMFirmware -VMName $VMName -FirstBootDevice $DVDDrive

# Set VLAN
Set-VMNetworkAdapterVlan -VMName $VMName -VlanId $VLANId -Access

# Set static MAC (optional, for DHCP reservation)
Set-VMNetworkAdapter -VMName $VMName -StaticMacAddress "00155D010A01"

# Start VM for OS installation
Start-VM -Name $VMName

Write-Host "Connect to VM console: vmconnect.exe localhost $VMName"
```

### 6.2 Guest OS Configuration (Post-Install)

```powershell
# Run inside the Clinical VM
$ClinicalIP  = "192.168.10.10"
$SubnetMask  = 24
$Gateway     = "192.168.10.1"
$DNSPrimary  = "192.168.20.5"

New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress $ClinicalIP `
  -PrefixLength $SubnetMask -DefaultGateway $Gateway
Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ServerAddresses $DNSPrimary

Rename-Computer -NewName "CLINICAL-VM-01" -Restart
# After reboot:
Add-Computer -DomainName "corp.clinical.local" -Credential (Get-Credential) -Restart

# Enable RDP
Set-ItemProperty -Path "HKLM:\System\CurrentControlSet\Control\Terminal Server" `
  -Name "fDenyTSConnections" -Value 0
Enable-NetFirewallRule -DisplayGroup "Remote Desktop"

# Windows Defender configuration
Set-MpPreference -DisableRealtimeMonitoring $false
Set-MpPreference -MAPSReporting Advanced
Set-MpPreference -SubmitSamplesConsent SendAllSamples
Update-MpSignature
```

---

## Step 7 – Install Azure Arc Agent on Clinical VM

```powershell
# Inside the Clinical VM
# Download Arc agent
Invoke-WebRequest -Uri "https://aka.ms/azcmagent-windows" `
  -OutFile "$env:TEMP\install_windows_azcmagent.ps1"

& "$env:TEMP\install_windows_azcmagent.ps1"

# Connect to Arc (use same SP as host, or dedicated SP for VMs)
azcmagent connect `
  --service-principal-id     "<appId>" `
  --service-principal-secret "<password>" `
  --resource-group           "rg-clinical-poc" `
  --tenant-id                "<tenant-id>" `
  --location                 "uksouth" `
  --subscription-id          "<subscription-id>" `
  --resource-name            "clinical-vm-01" `
  --tags                     "Environment=POC,Department=Clinical,Role=ClinicalWorkstation"

azcmagent show
```

---

## Step 8 – Enable Azure Backup and Monitoring

### 8.1 Azure Backup (Recovery Services Vault)

```bash
# Create Recovery Services Vault
az backup vault create \
  --resource-group rg-clinical-poc \
  --name rsv-clinical-poc \
  --location uksouth

# Set backup redundancy to GRS (Geo-redundant)
az backup vault backup-properties set \
  --resource-group rg-clinical-poc \
  --name rsv-clinical-poc \
  --backup-storage-redundancy GeoRedundant \
  --soft-delete-feature-state Enable

# Create backup policy (daily, 30-day retention)
az backup policy create \
  --resource-group rg-clinical-poc \
  --vault-name rsv-clinical-poc \
  --name policy-clinical-vms \
  --policy @backup_policy.json

# Enable backup for Clinical VM (Arc-enabled server)
az backup protection enable-for-vm \
  --resource-group rg-clinical-poc \
  --vault-name rsv-clinical-poc \
  --vm clinical-vm-01 \
  --policy-name policy-clinical-vms
```

### 8.2 Azure Monitor and Log Analytics

```bash
# Create Log Analytics Workspace
az monitor log-analytics workspace create \
  --resource-group rg-clinical-poc \
  --workspace-name law-clinical-poc \
  --location uksouth \
  --sku PerGB2018 \
  --retention-time 90

# Get workspace ID and key
WORKSPACE_ID=$(az monitor log-analytics workspace show \
  --resource-group rg-clinical-poc \
  --workspace-name law-clinical-poc \
  --query customerId -o tsv)

WORKSPACE_KEY=$(az monitor log-analytics workspace get-shared-keys \
  --resource-group rg-clinical-poc \
  --workspace-name law-clinical-poc \
  --query primarySharedKey -o tsv)

# Connect Arc machines to Log Analytics
az connectedmachine extension create \
  --resource-group rg-clinical-poc \
  --machine-name az-local-host01 \
  --name MicrosoftMonitoringAgent \
  --type MicrosoftMonitoringAgent \
  --publisher Microsoft.EnterpriseCloud.Monitoring \
  --settings "{\"workspaceId\":\"$WORKSPACE_ID\"}" \
  --protected-settings "{\"workspaceKey\":\"$WORKSPACE_KEY\"}"

# Create alert: CPU > 90% for 5 mins
az monitor metrics alert create \
  --resource-group rg-clinical-poc \
  --name "alert-high-cpu" \
  --description "CPU over 90% for 5 minutes" \
  --scopes "/subscriptions/<sub>/resourceGroups/rg-clinical-poc/providers/Microsoft.HybridCompute/machines/clinical-vm-01" \
  --condition "avg Percentage CPU > 90" \
  --window-size 5m \
  --evaluation-frequency 1m \
  --action /subscriptions/<sub>/resourceGroups/rg-clinical-poc/providers/microsoft.insights/actionGroups/ag-clinical-alerts
```

---

## Step 9 – Validate Remote Access & Logs

### 9.1 AnyConnect Validation

```bash
# From remote client:
# 1. Open Cisco AnyConnect Secure Mobility Client
# 2. Server: 203.0.113.10
# 3. Group: ClinicalVPN
# 4. Auth: SAML (Entra ID MFA prompt will appear)
# 5. Post-connect: verify IP in 192.168.100.x range

# From ASA CLI:
show vpn-sessiondb anyconnect
show crypto ca certificates
show saml metadata CLINICAL-SAML
```

### 9.2 Log Validation

```powershell
# Check Azure Monitor logs via Log Analytics
# KQL query in Azure Portal → Log Analytics Workspace

Heartbeat
| where Computer contains "CLINICAL"
| where TimeGenerated > ago(1h)
| project Computer, TimeGenerated, OSType, Category
| order by TimeGenerated desc

SecurityEvent
| where TimeGenerated > ago(24h)
| where EventID in (4624, 4625, 4648, 4720, 4728)
| project TimeGenerated, Computer, EventID, Account, Activity
| order by TimeGenerated desc
```

---

## Step 10 – Security Testing

### 10.1 Pre-requisites

- Written authorisation from the system owner
- Change control ticket approved
- Test window: during agreed maintenance window
- SIEM/SOC team notified

### 10.2 Test Cases

| Test | Tool | Expected Result |
|------|------|----------------|
| Port scan ASA outside | nmap | Only 443/TCP open |
| Unauthenticated VPN access | curl | 403 / SAML redirect |
| VLAN 10→20 direct traffic | ping from VM | Blocked by ACL |
| BitLocker enforcement | Policy check | Enabled, XTS-AES-256 |
| Credential Guard | msinfo32 | Running |
| Arc agent connectivity | azcmagent show | Connected |
| Backup job validation | Azure Portal | Completed |
| Certificate validation | openssl s_client | Valid, TLS 1.2+ |

```bash
# Authorised port scan (from approved pentest host)
nmap -sS -sV -p 1-65535 203.0.113.10 -oN nmap_asa_outside.txt

# TLS verification
echo | openssl s_client -connect 203.0.113.10:443 -servername asa-clinical-poc.corp.clinical.local 2>/dev/null \
  | openssl x509 -noout -text | grep -E "Issuer|Not After|Subject"

# AnyConnect SAML redirect test
curl -v https://203.0.113.10/+CSCOE+/logon.html 2>&1 | grep -i "saml\|location\|302"
```

---

## Rollback Procedures

| Step | Rollback Action |
|------|----------------|
| Azure Arc registration | `azcmagent disconnect --force` |
| BitLocker | `Disable-BitLocker -MountPoint C:` (requires recovery key) |
| Hyper-V | Remove role via Server Manager |
| ASA config | `copy startup-config running-config` from backup |
| Terraform | `terraform destroy` with confirmation |

## Sign-Off

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Lead Engineer | | | |
| Security Officer | | | |
| Clinical Informatics | | | |
| Change Manager | | | |
