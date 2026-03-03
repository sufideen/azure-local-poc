# Azure Local POC – Clinical Deployment Runbook & Training Lab

> **For Consultants & Engineers** – a complete, runnable reference implementation for deploying
> Azure Local in a regulated clinical environment, with Cisco ASA firewall integration and
> full Azure Arc management plane.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Ansible](https://img.shields.io/badge/Ansible-2.15%2B-red.svg)](https://docs.ansible.com)
[![Terraform](https://img.shields.io/badge/Terraform-1.7%2B-7B42BC.svg)](https://www.terraform.io)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org)
[![ASA](https://img.shields.io/badge/Cisco%20ASA-9.18%2B-1BA0D7.svg)](https://www.cisco.com)

---

## What This Is

A **production-grade, fully automated deployment runbook** for an Azure Local Proof-of-Concept
in a clinical / regulated environment. Every step is documented **and** automated across three
toolchains — Python, Ansible, and Terraform — so consultants can choose the right tool for each
engagement and training participants can compare approaches side-by-side.

### Covers All 10 Deployment Steps

| # | Step | Automation |
|---|------|-----------|
| 1 | Dell Server – BIOS, Secure Boot, Firmware | Ansible (iDRAC Redfish API) |
| 2 | Windows Server 2022 Azure Edition | Ansible (WinRM) |
| 3 | Hyper-V, BitLocker (XTS-AES-256), HVCI/VBS | Ansible (WinRM) |
| 4 | Azure Arc host registration (`azcmagent`) | Ansible + Azure CLI |
| **5** | **Cisco ASA** – Interfaces, NAT, VLANs, AnyConnect SAML | **Python (Netmiko) + Ansible (cisco.asa)** |
| 6 | Clinical VM – Gen2 Hyper-V, TPM, Secure Boot | Ansible (WinRM) |
| 7 | Azure Arc agent on Clinical VM | Ansible (WinRM) |
| 8 | Azure Backup (RSV) + Log Analytics + KQL Alerts | Ansible + Terraform |
| 9 | Remote access & log validation | Python + Ansible |
| 10 | Security testing (authorised, non-destructive) | Python |

---

## Who Should Use This

| Role | How to Use |
|------|-----------|
| **Azure Consultant** | Reference architecture + Terraform modules for client engagements |
| **Network Engineer** | Cisco ASA config baseline + Python automation (`cisco_asa/`) |
| **Security Engineer** | Step 10 security test suite, HVCI/BitLocker/Arc policy patterns |
| **Training Instructor** | Step-by-step runbook with lab exercises (see `EXERCISES.md`) |
| **Graduate / Apprentice** | Follow the runbook manually first, then run the automation |

---

## Network Architecture

```
                         ┌─────────────────────────────────────┐
   Internet              │           Cisco ASA 5506-X           │
   ──────────────────────┤  Outside: 203.0.113.10/24            │
                         │  Inside:  192.168.10.1/24 (VLAN 10)  │
                         │  DMZ:     192.168.20.1/24 (VLAN 20)  │
                         └────────────┬────────────┬────────────┘
                                      │            │
                          ┌───────────┘            └──────────────┐
                          │ VLAN 10 (Clinical)    VLAN 20 (Mgmt)  │
                     ┌────┴──────────────────────────────────────┐
                     │          Dell PowerEdge R750               │
                     │   Windows Server 2022 Azure Edition        │
                     │   Hyper-V  │  BitLocker  │  HVCI/VBS      │
                     │   ┌─────────────────────────────────────┐ │
                     │   │  Clinical VM (Windows Server 2022)  │ │
                     │   │  IP: 192.168.10.10/24               │ │
                     │   │  Arc Agent │ BitLocker │ Defender   │ │
                     │   └─────────────────────────────────────┘ │
                     └──────────────────────────────────────────┘
                                         │
                              ┌──────────┴──────────┐
                              │      Azure Cloud      │
                              │  Azure Arc            │
                              │  Azure Monitor / LAW  │
                              │  Azure Backup (RSV)   │
                              │  Microsoft Entra ID   │
                              │  Azure Policy         │
                              └───────────────────────┘

   AnyConnect VPN ──► ASA Outside (443) ──► SAML ──► Entra ID ──► Clinical VLAN
```

### IP Addressing

| Network Segment     | Subnet            | Gateway        | VLAN |
|---------------------|-------------------|----------------|------|
| Clinical VMs        | 192.168.10.0/24   | 192.168.10.1   | 10   |
| Management          | 192.168.20.0/24   | 192.168.20.1   | 20   |
| AnyConnect VPN Pool | 192.168.100.0/24  | N/A            | N/A  |
| ASA Outside         | 203.0.113.10/24   | 203.0.113.1    | N/A  |

---

## Repository Structure

```
azure-local-poc/
├── README.md                        ← You are here
├── EXERCISES.md                     ← Lab exercises for training sessions
├── CONTRIBUTING.md                  ← Contribution guide
├── LICENSE
│
├── runbook/
│   └── RUNBOOK.md                   ← Full manual runbook (every CLI command)
│
├── cisco_asa/                       ← Step 5 – Cisco ASA (detailed below)
│   ├── asa_deploy.py                ← Python/Netmiko automation
│   ├── asa_config_baseline.txt      ← Annotated paste-ready baseline config
│   ├── config.yml                   ← Parameter file
│   └── requirements.txt
│
├── ansible/
│   ├── site.yml                     ← Master playbook (all 10 steps)
│   ├── requirements.yml             ← Ansible Galaxy collections
│   ├── inventory/
│   │   ├── hosts.yml                ← Edit with your IPs/hostnames
│   │   └── group_vars/
│   │       ├── all.yml              ← Non-sensitive variables
│   │       └── vault.yml.example    ← Secrets template (encrypt with vault)
│   └── playbooks/                   ← One playbook per step (01_ … 10_)
│
├── terraform/
│   ├── main.tf / variables.tf / outputs.tf / providers.tf
│   ├── terraform.tfvars.example     ← Copy → terraform.tfvars (never commit)
│   └── modules/
│       ├── arc/                     ← Entra ID SP + RBAC assignments
│       ├── monitoring/              ← LAW + KQL saved searches + metric alerts
│       └── backup/                  ← RSV + VM backup + file-share policies
│
└── scripts/
    ├── validate/validate_all.py     ← 12-point end-to-end validation suite
    └── security/security_test.py    ← Authorised Step 10 security checks
```

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/<your-org>/azure-local-poc.git
cd azure-local-poc
```

### 2. Set up secrets

```bash
# Ansible – copy the example vault and edit with real values, then encrypt
cp ansible/inventory/group_vars/vault.yml.example \
   ansible/inventory/group_vars/vault.yml
# Edit vault.yml (passwords, SP secret, etc.)
ansible-vault encrypt ansible/inventory/group_vars/vault.yml

# Terraform
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform.tfvars (subscription_id, tenant_id, etc.)
```

### 3. Install dependencies

```bash
# Python
pip install -r cisco_asa/requirements.txt

# Ansible collections
ansible-galaxy collection install -r ansible/requirements.yml

# Terraform providers
cd terraform && terraform init && cd ..
```

### 4. Deploy

**Full Ansible run (all 10 steps):**
```bash
ansible-playbook ansible/site.yml \
  -i ansible/inventory/hosts.yml \
  --ask-vault-pass
```

**Single step:**
```bash
ansible-playbook ansible/site.yml \
  -i ansible/inventory/hosts.yml \
  --ask-vault-pass --tags step5
```

**Terraform (Azure cloud resources):**
```bash
cd terraform
terraform plan -out=poc.plan
terraform apply poc.plan
```

**Python – Cisco ASA only:**
```bash
# Dry run (no device connection, generates config file)
python cisco_asa/asa_deploy.py --config cisco_asa/config.yml --dry-run

# Deploy specific steps
python cisco_asa/asa_deploy.py --config cisco_asa/config.yml \
  --step interfaces --step nat --step acl --step saml
```

### 5. Validate

```bash
python scripts/validate/validate_all.py \
  --config scripts/validate/validate_config.yml \
  --output validation_report.json
```

---

## Cisco ASA – Configuration Detail

The ASA automation covers the full configuration lifecycle:

| Area | Config Detail |
|------|--------------|
| **Interfaces** | GE1/1=outside (203.0.113.10), GE1/2=inside/VLAN10, GE1/3=DMZ/VLAN20 |
| **NAT** | PAT for clinical+DMZ egress; identity no-NAT for VPN↔clinical traffic |
| **ACLs** | 4 named ACLs; VLAN10→VLAN20 blocked; all deny rules log |
| **AnyConnect** | SAML authentication, split tunnel, 480-min session, DTLS enabled |
| **SAML (Entra ID)** | Full Enterprise App config steps; cert import; group assignment |
| **TLS Policy** | `ssl server-version tlsv1.2`, `ssl cipher tlsv1.2 high`, DH-group14 |
| **SSH** | DMZ + VPN clients only; SSHv2; dh-group14-sha256 |
| **Syslog** | To 192.168.20.20:514/UDP → Log Analytics forwarder |
| **Threat Detection** | Basic + statistics + TCP-intercept rate limiting |

### Python deployer modes

```bash
--dry-run           # Print commands only, no device connection
--generate-only     # Write full config to .txt file
--step <name>       # Run one section (global/interfaces/nat/acl/saml/…)
# (no flags)        # Full deployment with pre/post config backup + verification
```

---

## Lab Exercises

See [`EXERCISES.md`](EXERCISES.md) for structured hands-on exercises:

1. **Manual first** – configure VLAN isolation manually, test with ping, then compare to automation output
2. **Extend the ASA deployer** – add a `qos` step to `asa_deploy.py`
3. **Write a Terraform module** – add a second Clinical VM to the backup module
4. **KQL challenge** – write an alert for ASA syslog `%ASA-4-106023` (denied connection) events
5. **Security test interpretation** – run `security_test.py`, fix one finding, re-run to confirm pass
6. **Break/fix lab** – deliberately misconfigure the ACL, validate failure, fix, validate pass

---

## Security Notes

- `vault.yml` and `terraform.tfvars` are **git-ignored** – never commit credentials
- Step 10 requires **written authorisation** before execution
- ASA baseline uses `service password-encryption` – upgrade to Type 9 (PBKDF2) in production
- Self-signed cert in baseline – replace with CA-signed cert before production go-live
- SNMP community string in `config.yml` is a placeholder – use ansible-vault for real values

---

## Prerequisites

| Tool | Min Version | Purpose |
|------|------------|---------|
| Python | 3.11 | ASA automation, validation |
| Ansible | 2.15 | Steps 1–10 playbooks |
| Terraform | 1.7 | Azure cloud resources |
| Azure CLI | 2.55 | Arc registration |
| Cisco ASA OS | 9.18 | Target firewall |
| Windows Server | 2022 Azure Edition | Hyper-V host + guest |

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). PRs welcome for:
- Other Cisco models (FTD, ISE integration)
- NHS FHIR / HL7 network segmentation patterns
- Azure Landing Zone integration
- VMware ESXi / Nutanix AHV alternatives

---

## Licence

MIT – free to use on client engagements, internal POCs and training sessions.
