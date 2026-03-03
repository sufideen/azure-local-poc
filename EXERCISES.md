# Lab Exercises – Azure Local POC Training

**Audience:** Consultants, Graduate Engineers, Security Analysts
**Pre-requisites:** Read `README.md` and `runbook/RUNBOOK.md` first.

> Complete exercises **in order**. Each builds on the previous.
> Expected time per exercise is shown in brackets.

---

## Exercise 1 – Manual VLAN Isolation Test \[30 min]

**Learning objective:** Understand why VLAN segmentation matters and verify ACL enforcement.

### Task
1. From the Clinical VM (192.168.10.10), attempt to ping the Management VLAN gateway (192.168.20.1):
   ```cmd
   ping 192.168.20.1
   ```
2. Capture the ASA syslog entry showing the denied packet:
   ```
   show log | include 192.168.10.10
   ```
3. Identify which ACL line blocked it (`ACL-INSIDE-OUT` deny rule).
4. Temporarily permit ICMP from VLAN 10 to VLAN 20 by adding an ACE before the deny rule.
5. Re-test ping – it should now succeed.
6. **Remove the temporary rule** and verify the ping fails again.
7. Document the ACL line numbers and explain the implicit deny vs. explicit deny-log difference.

### Questions
- Why does the existing ACL use `deny ip any any log` instead of relying on the implicit deny?
- What would happen if the deny rule was removed but traffic was still blocked?

---

## Exercise 2 – Extend the ASA Python Deployer \[45 min]

**Learning objective:** Understand the `ASACommandBuilder` pattern and add a new config section.

### Task
Add a `ntp_auth` step to `cisco_asa/asa_deploy.py` that configures NTP authentication:

```python
def ntp_auth(self) -> list[str]:
    # Return commands to:
    # 1. Generate NTP authentication key (MD5, key-id 1)
    # 2. Mark key 1 as trusted
    # 3. Configure ntp server with the key
    pass
```

Requirements:
- The NTP key value must come from `self.cfg` (add a new field `ntp_key: str`)
- Add `ntp_auth` to `all_steps()` after `ntp`
- Add `ntp_key` to `config.yml`
- Test with `--dry-run` and confirm the commands are printed correctly

### Stretch goal
Add a `verify_ntp` method to `ASADeployer` that runs `show ntp status` and asserts
the output contains `synchronized`.

---

## Exercise 3 – Write a Terraform Module \[60 min]

**Learning objective:** Build a reusable Terraform module following the existing pattern.

### Task
Create `terraform/modules/clinical_vm_backup/main.tf` that:

1. Accepts these input variables:
   - `vault_id` (string) – Recovery Services Vault resource ID
   - `vm_arc_resource_id` (string) – Arc machine resource ID
   - `policy_id` (string) – Backup policy ID
   - `common_tags` (map of string)

2. Creates a `azurerm_backup_protected_vm` resource associating the Arc VM with the vault and policy.

3. Outputs:
   - `backup_item_id` – the protected item resource ID

4. Wire it up in `terraform/main.tf` using outputs from the `backup` and `arc` modules.

### Hints
- Arc VMs use `azurerm_backup_protected_vm` with `source_vm_id` pointing to the Arc machine ID
- Check the Terraform azurerm provider docs for the correct resource type

---

## Exercise 4 – KQL Alert Engineering \[30 min]

**Learning objective:** Write production-quality KQL and wire it to an Azure Monitor alert.

### Task
The ASA syslog message `%ASA-4-106023` fires when a packet is explicitly denied by an ACL
with the `log` keyword. Write a KQL query that:

1. Queries the `Syslog` table
2. Filters to `ProcessName contains "ASA"` and message contains `106023`
3. Parses the source IP, destination IP, and destination port from the message
4. Returns rows where the source IP is **not** in the VPN pool (192.168.100.0/24) or Clinical VLAN (192.168.10.0/24) — i.e., external denied traffic
5. Summarises by source IP with a count, over 1-hour bins

Then add this query to `terraform/modules/monitoring/main.tf` as a new
`azurerm_log_analytics_saved_search` resource named `ASADeniedExternalTraffic`.

### Questions
- At what threshold would you set an alert on this query? Justify your answer.
- What additional context would you extract from the syslog message?

---

## Exercise 5 – Security Test Interpretation \[45 min]

**Learning objective:** Run the security suite, understand findings, and verify a fix.

### Setup
Deliberately weaken the ASA TLS policy:
```
! On ASA CLI:
ssl server-version tlsv1
```
This allows TLS 1.0.

### Task
1. Run the security test script:
   ```bash
   python scripts/security/security_test.py \
     --asa-ip 203.0.113.10 \
     --output security_before.json
   ```
2. Identify the failing test and its severity rating.
3. Fix the issue (restore `ssl server-version tlsv1.2` on the ASA).
4. Re-run the test:
   ```bash
   python scripts/security/security_test.py \
     --asa-ip 203.0.113.10 \
     --output security_after.json
   ```
5. Compare the two JSON reports and confirm the finding is resolved.
6. Write a one-paragraph risk statement as if reporting to a clinical CISO.

---

## Exercise 6 – Break/Fix Lab \[60 min]

**Learning objective:** Diagnose and fix a misconfigured ACL using validation tooling.

### Setup (instructor provides pre-broken environment)
The validation script reports:
```
[FAIL] Step 9 – Heartbeats in Log Analytics (1h)  → No heartbeats found
[FAIL] Step 5 – AnyConnect SAML redirect           → HTTP 500
```

### Task
Using only `show` commands on the ASA and the validation script output:

1. Diagnose the root cause of each failure (there is one config error causing both).
2. Identify the misconfigured element without looking at the running config directly — use only:
   - `show access-list`
   - `show nat detail`
   - `show conn`
   - `show log`
3. Apply the fix.
4. Re-run `python scripts/validate/validate_all.py` and confirm all checks pass.
5. Document your diagnostic methodology (what commands, what you looked for, what the fix was).

> **Instructor note:** The break is removing the `nat (outside,inside) source static OBJ-VPN-POOL …`
> no-NAT rule. Without it, VPN client traffic gets PAT'd to the outside IP, breaking both
> AnyConnect SAML callbacks and the Log Analytics agent outbound connection from the Clinical VM.

---

## Exercise 7 – Ansible Vault Security \[20 min]

**Learning objective:** Understand how to properly manage secrets in Ansible.

### Task
1. Create a vault-encrypted version of the example vault file:
   ```bash
   cp ansible/inventory/group_vars/vault.yml.example \
      ansible/inventory/group_vars/vault.yml
   ansible-vault encrypt ansible/inventory/group_vars/vault.yml
   ```
2. View the encrypted file with `cat` – confirm it is not readable.
3. Edit one value using `ansible-vault edit`.
4. Run a syntax check using the vault password:
   ```bash
   ansible-playbook ansible/site.yml \
     -i ansible/inventory/hosts.yml \
     --ask-vault-pass --syntax-check
   ```
5. Explain why `vault.yml` is in `.gitignore` and what would happen if it were committed
   (even encrypted).

---

## Reference – ASA Verification Commands

Use these during all exercises to confirm state:

```
show interface ip brief           # Interface IPs and status
show access-list                  # All ACEs with hit counts
show nat detail                   # NAT rules and translations
show conn count                   # Active connections
show vpn-sessiondb anyconnect     # Active AnyConnect sessions
show crypto ca certificates       # Installed certificates
show log | include 4-106023       # Denied ACL hits
show saml metadata CLINICAL-SAML  # SAML IdP metadata
show webvpn group-policy          # Group policy assignments
```
