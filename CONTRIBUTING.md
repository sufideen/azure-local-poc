# Contributing to azure-local-poc

Thanks for contributing. This repo is used by consultants on live engagements and by
training cohorts, so quality and clarity matter — a new engineer should be able to follow
every step without prior knowledge of the environment.

## What We Welcome

- **Fixes** to incorrect commands, outdated API versions, or broken automation
- **New firewall variants** (Cisco FTD, Palo Alto, FortiGate) following the same pattern
- **Alternative hypervisors** (VMware ESXi, Nutanix AHV) alongside the Hyper-V path
- **Additional training exercises** in `EXERCISES.md`
- **Improved KQL queries** in the monitoring Terraform module
- **NHS / HL7 / FHIR** specific network segmentation or compliance additions

## What We Do Not Accept

- Credentials, real IP addresses, tenant IDs, or licence keys in any file
- Destructive security tests (DoS, exploitation, brute-force tooling)
- Vendor-specific content that cannot be reproduced in a lab without paid licences

## Branching and PRs

```
main          ← stable, used in training sessions
feature/<name>  ← new features
fix/<name>      ← bug fixes
exercise/<name> ← new lab exercises
```

PRs must:
1. Pass `ansible-playbook --syntax-check` on all modified playbooks
2. Pass `python -m py_compile` on all modified Python files
3. Pass `terraform validate` in the `terraform/` directory
4. Not introduce any secrets (enforced by `.gitignore` and PR review)
5. Include an update to `EXERCISES.md` if the change affects training workflows

## File Conventions

| File type | Convention |
|-----------|-----------|
| Ansible playbooks | YAML, 2-space indent, `name:` on every task |
| Ansible variables | `snake_case`, descriptive names |
| Python | PEP 8, type hints on all public functions, docstrings on all classes |
| Terraform | `snake_case` resource names, `common_tags` on every resource |
| ASA config | Comments on every section with `!`, placeholders in `<UPPER_CASE>` |

## Running Checks Locally

```bash
# Ansible syntax check (all playbooks)
for f in ansible/playbooks/*.yml; do
  ansible-playbook "$f" -i ansible/inventory/hosts.yml --syntax-check
done

# Python compile check
python -m py_compile cisco_asa/asa_deploy.py
python -m py_compile scripts/validate/validate_all.py
python -m py_compile scripts/security/security_test.py

# Terraform validate
cd terraform && terraform init -backend=false && terraform validate
```
