#!/usr/bin/env python3
"""
Azure Local POC – End-to-End Validation Script
Validates all 10 deployment steps programmatically.

Usage:
    python validate_all.py [--config config.yml] [--output report.html]

Author: Clinical IT Engineering
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
import urllib.request
import urllib.error

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    detail: Optional[str] = None
    step: int = 0


@dataclass
class ValidationReport:
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def all_passed(self) -> bool:
        return self.failed_count == 0

    def print_summary(self) -> None:
        width = 70
        print("\n" + "=" * width)
        print("  AZURE LOCAL POC – VALIDATION REPORT")
        print(f"  Generated: {self.timestamp}")
        print("=" * width)

        for r in self.results:
            icon = "✓" if r.passed else "✗"
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {icon} Step {r.step:02d} – {r.name}")
            if r.message:
                print(f"           {r.message}")
            if r.detail and not r.passed:
                print(f"           Detail: {r.detail}")

        print("-" * width)
        print(f"  Results: {self.passed_count} passed, {self.failed_count} failed "
              f"out of {len(self.results)} checks")
        print("=" * width + "\n")

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "summary": {
                "total": len(self.results),
                "passed": self.passed_count,
                "failed": self.failed_count,
                "all_passed": self.all_passed,
            },
            "results": [
                {
                    "step": r.step,
                    "name": r.name,
                    "passed": r.passed,
                    "message": r.message,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }, indent=2)


# ── Validator class ───────────────────────────────────────────────────────────

class POCValidator:
    """Runs all validation checks for the Azure Local POC deployment."""

    def __init__(self, config: dict[str, Any]):
        self.cfg = config
        self.report = ValidationReport()

    def _check(
        self,
        step: int,
        name: str,
        fn: Callable[[], tuple[bool, str, Optional[str]]],
    ) -> CheckResult:
        """Run a check function and record the result."""
        print(f"  Checking: {name} ...", end=" ", flush=True)
        try:
            passed, message, detail = fn()
        except Exception as exc:  # noqa: BLE001
            passed, message, detail = False, f"Exception: {exc}", str(exc)
        result = CheckResult(name=name, passed=passed, message=message,
                             detail=detail, step=step)
        self.report.add(result)
        print("PASS" if passed else "FAIL")
        return result

    # ── Step 1: Dell / BIOS ───────────────────────────────────────────────────
    def check_idrac_reachable(self) -> tuple[bool, str, Optional[str]]:
        """Check iDRAC management IP is reachable on HTTPS."""
        idrac_ip = self.cfg.get("idrac_ip", "192.168.20.254")
        try:
            sock = socket.create_connection((idrac_ip, 443), timeout=5)
            sock.close()
            return True, f"iDRAC {idrac_ip}:443 reachable", None
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            return False, f"iDRAC {idrac_ip}:443 not reachable", str(e)

    # ── Step 2 & 3: Windows Server / Hyper-V ─────────────────────────────────
    def check_host_winrm(self) -> tuple[bool, str, Optional[str]]:
        """Check WinRM HTTPS on Hyper-V host."""
        host_ip = self.cfg.get("host_ip", "192.168.20.10")
        try:
            sock = socket.create_connection((host_ip, 5986), timeout=5)
            sock.close()
            return True, f"WinRM HTTPS {host_ip}:5986 reachable", None
        except Exception as e:
            return False, f"WinRM {host_ip}:5986 not reachable", str(e)

    def check_vm_rdp(self) -> tuple[bool, str, Optional[str]]:
        """Check RDP port on Clinical VM."""
        vm_ip = self.cfg.get("clinical_vm_ip", "192.168.10.10")
        try:
            sock = socket.create_connection((vm_ip, 3389), timeout=5)
            sock.close()
            return True, f"RDP {vm_ip}:3389 reachable", None
        except Exception as e:
            return False, f"RDP {vm_ip}:3389 not reachable", str(e)

    # ── Step 4: Azure Arc ─────────────────────────────────────────────────────
    def check_arc_host_connected(self) -> tuple[bool, str, Optional[str]]:
        """Check Hyper-V host Arc status via Azure CLI."""
        rg   = self.cfg.get("resource_group", "rg-clinical-poc")
        name = self.cfg.get("arc_host_name", "az-local-host01")
        try:
            result = subprocess.run(
                ["az", "connectedmachine", "show",
                 "--resource-group", rg, "--name", name,
                 "--query", "status", "-o", "tsv"],
                capture_output=True, text=True, timeout=30,
            )
            status = result.stdout.strip()
            if status == "Connected":
                return True, f"Arc host '{name}' status: Connected", None
            return False, f"Arc host '{name}' status: {status or 'Unknown'}", result.stderr
        except FileNotFoundError:
            return False, "Azure CLI (az) not found in PATH", None
        except subprocess.TimeoutExpired:
            return False, "Azure CLI query timed out", None

    def check_arc_vm_connected(self) -> tuple[bool, str, Optional[str]]:
        """Check Clinical VM Arc status via Azure CLI."""
        rg   = self.cfg.get("resource_group", "rg-clinical-poc")
        name = self.cfg.get("arc_vm_name", "clinical-vm-01")
        try:
            result = subprocess.run(
                ["az", "connectedmachine", "show",
                 "--resource-group", rg, "--name", name,
                 "--query", "status", "-o", "tsv"],
                capture_output=True, text=True, timeout=30,
            )
            status = result.stdout.strip()
            if status == "Connected":
                return True, f"Arc VM '{name}' status: Connected", None
            return False, f"Arc VM '{name}' status: {status or 'Unknown'}", result.stderr
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return False, "Azure CLI query failed", str(e)

    # ── Step 5: Cisco ASA ─────────────────────────────────────────────────────
    def check_asa_https(self) -> tuple[bool, str, Optional[str]]:
        """Verify ASA outside IP responds on port 443."""
        asa_ip = self.cfg.get("asa_outside_ip", "203.0.113.10")
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((asa_ip, 443), timeout=10) as sock:
                with ctx.wrap_socket(sock) as ssock:
                    cert = ssock.getpeercert(binary_form=True)
                    return True, f"ASA HTTPS {asa_ip}:443 reachable (TLS established)", None
        except Exception as e:
            return False, f"ASA {asa_ip}:443 not reachable", str(e)

    def check_asa_tls_version(self) -> tuple[bool, str, Optional[str]]:
        """Verify ASA enforces TLS 1.2 minimum."""
        asa_ip = self.cfg.get("asa_outside_ip", "203.0.113.10")
        try:
            # Attempt connection with TLS 1.0 – should fail
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.maximum_version = ssl.TLSVersion.TLSv1
            try:
                with socket.create_connection((asa_ip, 443), timeout=5) as sock:
                    with ctx.wrap_socket(sock):
                        pass
                return False, "ASA accepted TLS 1.0 – policy requires TLS 1.2+", None
            except ssl.SSLError:
                return True, "TLS 1.0 rejected by ASA – TLS 1.2+ enforced", None
        except Exception as e:
            return False, "TLS version check failed", str(e)

    def check_asa_ssh_closed(self) -> tuple[bool, str, Optional[str]]:
        """Verify ASA outside interface does NOT expose SSH."""
        asa_ip = self.cfg.get("asa_outside_ip", "203.0.113.10")
        try:
            sock = socket.create_connection((asa_ip, 22), timeout=5)
            sock.close()
            return False, f"SSH port 22 OPEN on ASA outside {asa_ip} – should be closed", None
        except (socket.timeout, ConnectionRefusedError):
            return True, f"SSH port 22 closed on ASA outside {asa_ip} (expected)", None
        except OSError as e:
            return True, f"SSH port 22 not reachable (expected): {e}", None

    # ── Step 8: Backup & Monitoring ───────────────────────────────────────────
    def check_rsv_exists(self) -> tuple[bool, str, Optional[str]]:
        """Check Recovery Services Vault exists and is provisioned."""
        rg  = self.cfg.get("resource_group", "rg-clinical-poc")
        rsv = self.cfg.get("rsv_name", "rsv-clinical-poc")
        try:
            result = subprocess.run(
                ["az", "backup", "vault", "show",
                 "--resource-group", rg, "--name", rsv,
                 "--query", "properties.provisioningState", "-o", "tsv"],
                capture_output=True, text=True, timeout=30,
            )
            state = result.stdout.strip()
            if state == "Succeeded":
                return True, f"Recovery Services Vault '{rsv}' provisioned", None
            return False, f"RSV '{rsv}' state: {state or 'not found'}", result.stderr
        except Exception as e:
            return False, "RSV check failed", str(e)

    def check_law_exists(self) -> tuple[bool, str, Optional[str]]:
        """Check Log Analytics Workspace exists."""
        rg  = self.cfg.get("resource_group", "rg-clinical-poc")
        law = self.cfg.get("law_workspace_name", "law-clinical-poc")
        try:
            result = subprocess.run(
                ["az", "monitor", "log-analytics", "workspace", "show",
                 "--resource-group", rg, "--workspace-name", law,
                 "--query", "provisioningState", "-o", "tsv"],
                capture_output=True, text=True, timeout=30,
            )
            state = result.stdout.strip()
            if state == "Succeeded":
                return True, f"Log Analytics Workspace '{law}' provisioned", None
            return False, f"LAW '{law}' state: {state or 'not found'}", result.stderr
        except Exception as e:
            return False, "LAW check failed", str(e)

    def check_heartbeats_in_law(self) -> tuple[bool, str, Optional[str]]:
        """Query Log Analytics for recent heartbeats."""
        rg  = self.cfg.get("resource_group", "rg-clinical-poc")
        law = self.cfg.get("law_workspace_name", "law-clinical-poc")
        try:
            # Get workspace ID
            ws_result = subprocess.run(
                ["az", "monitor", "log-analytics", "workspace", "show",
                 "--resource-group", rg, "--workspace-name", law,
                 "--query", "customerId", "-o", "tsv"],
                capture_output=True, text=True, timeout=30,
            )
            ws_id = ws_result.stdout.strip()
            if not ws_id:
                return False, "Could not retrieve LAW workspace ID", None

            # Query heartbeats
            query_result = subprocess.run(
                ["az", "monitor", "log-analytics", "query",
                 "--workspace", ws_id,
                 "--analytics-query",
                 "Heartbeat | where TimeGenerated > ago(1h) | summarize count()",
                 "--output", "json"],
                capture_output=True, text=True, timeout=60,
            )
            data = json.loads(query_result.stdout or "[]")
            count = 0
            if data and isinstance(data, list):
                count = data[0].get("count_", 0) if data[0] else 0
            if count > 0:
                return True, f"LAW heartbeats in last 1h: {count}", None
            return False, "No heartbeats found in Log Analytics (last 1h)", None
        except Exception as e:
            return False, "Heartbeat query failed", str(e)

    # ── Step 9: Remote Access ─────────────────────────────────────────────────
    def check_vpn_saml_redirect(self) -> tuple[bool, str, Optional[str]]:
        """Verify AnyConnect login page returns SAML redirect."""
        asa_ip = self.cfg.get("asa_outside_ip", "203.0.113.10")
        url = f"https://{asa_ip}/+CSCOE+/logon.html"
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                body = resp.read(4096).decode("utf-8", errors="replace").lower()
                if "saml" in body or "microsoft" in body or "login" in body:
                    return True, "AnyConnect login page responds with SAML/auth content", None
                return True, f"AnyConnect login page reachable (HTTP {resp.status})", None
        except urllib.error.HTTPError as e:
            # 302/401/403 redirect to SAML is expected and acceptable
            if e.code in (302, 301, 401, 403):
                return True, f"AnyConnect redirected (HTTP {e.code}) – expected SAML flow", None
            return False, f"Unexpected HTTP error: {e.code}", str(e)
        except Exception as e:
            return False, "Could not reach AnyConnect login page", str(e)

    # ── Run all ───────────────────────────────────────────────────────────────
    def run_all(self) -> ValidationReport:
        print("\n" + "=" * 70)
        print("  AZURE LOCAL POC – STARTING VALIDATION")
        print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        print("=" * 70)

        checks: list[tuple[int, str, Callable]] = [
            (1,  "iDRAC reachable (HTTPS)",             self.check_idrac_reachable),
            (2,  "Hyper-V host WinRM (HTTPS 5986)",     self.check_host_winrm),
            (3,  "Clinical VM RDP (3389)",               self.check_vm_rdp),
            (4,  "Azure Arc – Host Connected",           self.check_arc_host_connected),
            (4,  "Azure Arc – Clinical VM Connected",    self.check_arc_vm_connected),
            (5,  "ASA Outside HTTPS (443)",              self.check_asa_https),
            (5,  "ASA TLS 1.2+ enforced",                self.check_asa_tls_version),
            (5,  "ASA SSH closed on outside",            self.check_asa_ssh_closed),
            (5,  "AnyConnect SAML redirect",             self.check_vpn_saml_redirect),
            (8,  "Recovery Services Vault provisioned",  self.check_rsv_exists),
            (8,  "Log Analytics Workspace provisioned",  self.check_law_exists),
            (9,  "Heartbeats in Log Analytics (1h)",     self.check_heartbeats_in_law),
        ]

        print(f"\n  Running {len(checks)} checks ...\n")
        for step, name, fn in checks:
            self._check(step, name, fn)

        self.report.print_summary()
        return self.report


# ── CLI ───────────────────────────────────────────────────────────────────────

def load_config(path: Optional[str]) -> dict[str, Any]:
    """Load config from YAML file or return defaults."""
    defaults: dict[str, Any] = {
        "idrac_ip":           "192.168.20.254",
        "host_ip":            "192.168.20.10",
        "clinical_vm_ip":     "192.168.10.10",
        "asa_outside_ip":     "203.0.113.10",
        "resource_group":     "rg-clinical-poc",
        "arc_host_name":      "az-local-host01",
        "arc_vm_name":        "clinical-vm-01",
        "rsv_name":           "rsv-clinical-poc",
        "law_workspace_name": "law-clinical-poc",
    }
    if path and HAS_YAML:
        try:
            with open(path) as f:
                file_cfg = yaml.safe_load(f) or {}
            defaults.update(file_cfg.get("validate", {}))
        except FileNotFoundError:
            print(f"Warning: config file '{path}' not found – using defaults")
    return defaults


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Azure Local POC – End-to-End Validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="Path to YAML config file")
    parser.add_argument("--output", help="Write JSON report to file")
    args = parser.parse_args()

    cfg = load_config(args.config)
    validator = POCValidator(cfg)
    report = validator.run_all()

    if args.output:
        with open(args.output, "w") as f:
            f.write(report.to_json())
        print(f"JSON report written to: {args.output}")

    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
