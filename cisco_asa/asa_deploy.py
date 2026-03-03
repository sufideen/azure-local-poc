#!/usr/bin/env python3
"""
Cisco ASA Automated Deployment Script
Azure Local POC – Clinical Environment

Automates complete ASA configuration including:
  - Interface setup (Outside, Inside/VLAN10, DMZ/VLAN20)
  - NAT rules (PAT for clinical/DMZ, no-NAT for VPN)
  - ACLs (outside-in, inside-out, VPN-to-inside, DMZ-to-inside)
  - AnyConnect with SAML (Microsoft Entra ID)
  - Group policy, tunnel-group, VPN pool
  - Syslog, SSH, threat detection
  - Backup and verification

Usage:
    pip install -r requirements.txt
    python asa_deploy.py --config config.yml [--dry-run] [--step <step_name>]

Author: Clinical IT Engineering
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException
from netmiko.exceptions import NetmikoBaseException

try:
    import colorlog
    _COLORLOG = True
except ImportError:
    _COLORLOG = False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(debug: bool = False) -> logging.Logger:
    level = logging.DEBUG if debug else logging.INFO
    if _COLORLOG:
        handler = colorlog.StreamHandler()
        handler.setFormatter(colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s [%(levelname)-8s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan", "INFO": "green",
                "WARNING": "yellow", "ERROR": "red", "CRITICAL": "bold_red",
            },
        ))
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(message)s", "%Y-%m-%d %H:%M:%S"
        ))

    log = logging.getLogger("asa_deploy")
    log.setLevel(level)
    log.addHandler(handler)

    # File handler
    fh = logging.FileHandler("asa_deploy.log")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(message)s", "%Y-%m-%d %H:%M:%S"
    ))
    log.addHandler(fh)
    return log


log = setup_logging()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ASAConfig:
    """Holds all parameters for the ASA deployment."""
    # Connection
    host: str
    username: str
    password: str
    enable_password: str
    port: int = 22

    # Identity
    hostname: str = "ASA-CLINICAL-POC"
    domain_name: str = "corp.clinical.local"

    # Interfaces
    outside_ip: str = "203.0.113.10"
    outside_mask: str = "255.255.255.0"
    outside_gw: str = "203.0.113.1"
    inside_ip: str = "192.168.10.1"
    inside_mask: str = "255.255.255.0"
    dmz_ip: str = "192.168.20.1"
    dmz_mask: str = "255.255.255.0"

    # VPN
    vpn_pool_start: str = "192.168.100.10"
    vpn_pool_end: str = "192.168.100.100"
    vpn_pool_mask: str = "255.255.255.0"
    vpn_dns: str = "192.168.20.5"
    vpn_tunnel_group: str = "CLINICAL-SAML"
    vpn_group_alias: str = "ClinicalVPN"

    # SAML / Entra ID
    tenant_id: str = ""
    asa_public_fqdn: str = "asa-clinical-poc.corp.clinical.local"

    # Syslog
    syslog_server: str = "192.168.20.20"

    # SNMP
    snmp_community: str = "CHANGE_ME"
    snmp_server: str = "192.168.20.20"

    # Crypto
    rsa_modulus: int = 4096
    rsa_label: str = "ASA-VPN-KEY"

    @classmethod
    def from_yaml(cls, path: str, password: str, enable_password: str) -> "ASAConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        asa = data.get("asa", {})
        return cls(
            host=asa["host"],
            username=asa["username"],
            password=password,
            enable_password=enable_password,
            port=asa.get("port", 22),
            hostname=asa.get("hostname", "ASA-CLINICAL-POC"),
            domain_name=asa.get("domain_name", "corp.clinical.local"),
            outside_ip=asa.get("outside_ip", "203.0.113.10"),
            outside_mask=asa.get("outside_mask", "255.255.255.0"),
            outside_gw=asa.get("outside_gw", "203.0.113.1"),
            inside_ip=asa.get("inside_ip", "192.168.10.1"),
            inside_mask=asa.get("inside_mask", "255.255.255.0"),
            dmz_ip=asa.get("dmz_ip", "192.168.20.1"),
            dmz_mask=asa.get("dmz_mask", "255.255.255.0"),
            vpn_pool_start=asa.get("vpn_pool_start", "192.168.100.10"),
            vpn_pool_end=asa.get("vpn_pool_end", "192.168.100.100"),
            vpn_pool_mask=asa.get("vpn_pool_mask", "255.255.255.0"),
            vpn_dns=asa.get("vpn_dns", "192.168.20.5"),
            vpn_tunnel_group=asa.get("vpn_tunnel_group", "CLINICAL-SAML"),
            vpn_group_alias=asa.get("vpn_group_alias", "ClinicalVPN"),
            tenant_id=asa.get("tenant_id", ""),
            asa_public_fqdn=asa.get("asa_public_fqdn", "asa-clinical-poc.corp.clinical.local"),
            syslog_server=asa.get("syslog_server", "192.168.20.20"),
            snmp_community=asa.get("snmp_community", "CHANGE_ME"),
            snmp_server=asa.get("snmp_server", "192.168.20.20"),
            rsa_modulus=asa.get("rsa_modulus", 4096),
            rsa_label=asa.get("rsa_label", "ASA-VPN-KEY"),
        )


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------

class ASACommandBuilder:
    """Builds ordered lists of ASA configuration commands."""

    def __init__(self, cfg: ASAConfig):
        self.cfg = cfg

    def global_settings(self) -> list[str]:
        c = self.cfg
        return [
            f"hostname {c.hostname}",
            f"domain-name {c.domain_name}",
            "dns server-group DefaultDNS",
            " name-server 8.8.8.8",
            " name-server 8.8.4.4",
            "clock timezone UTC 0",
            "clock summer-time BST recurring last Sun Mar 1:00 last Sun Oct 1:00",
            "no http server enable",  # Disable ASDM HTTP if not needed
            "service password-encryption",
        ]

    def interfaces(self) -> list[str]:
        c = self.cfg
        return [
            "interface GigabitEthernet1/1",
            " description OUTSIDE-INTERNET",
            " nameif outside",
            " security-level 0",
            f" ip address {c.outside_ip} {c.outside_mask}",
            " no shutdown",
            "interface GigabitEthernet1/2",
            " description INSIDE-CLINICAL-VLAN10",
            " nameif inside",
            " security-level 100",
            f" ip address {c.inside_ip} {c.inside_mask}",
            " no shutdown",
            "interface GigabitEthernet1/3",
            " description DMZ-MANAGEMENT-VLAN20",
            " nameif dmz",
            " security-level 50",
            f" ip address {c.dmz_ip} {c.dmz_mask}",
            " no shutdown",
            "interface GigabitEthernet1/4",
            " description UNUSED",
            " shutdown",
        ]

    def routing(self) -> list[str]:
        c = self.cfg
        return [
            f"route outside 0.0.0.0 0.0.0.0 {c.outside_gw} 1",
        ]

    def network_objects(self) -> list[str]:
        c = self.cfg
        # Derive network addresses from IPs
        inside_net = ".".join(c.inside_ip.split(".")[:3]) + ".0"
        dmz_net = ".".join(c.dmz_ip.split(".")[:3]) + ".0"
        vpn_net = ".".join(c.vpn_pool_start.split(".")[:3]) + ".0"
        return [
            "object network OBJ-INSIDE-NET",
            f" subnet {inside_net} {c.inside_mask}",
            " description Clinical VLAN 10",
            "object network OBJ-DMZ-NET",
            f" subnet {dmz_net} {c.dmz_mask}",
            " description Management VLAN 20",
            "object network OBJ-VPN-POOL",
            f" subnet {vpn_net} {c.vpn_pool_mask}",
            " description AnyConnect VPN Client Pool",
            f"object network OBJ-ASA-OUTSIDE",
            f" host {c.outside_ip}",
            " description ASA Outside Public IP",
        ]

    def nat_rules(self) -> list[str]:
        return [
            "! PAT: Clinical clients to internet",
            "nat (inside,outside) after-auto source dynamic OBJ-INSIDE-NET interface",
            "! PAT: DMZ/Management to internet",
            "nat (dmz,outside) after-auto source dynamic OBJ-DMZ-NET interface",
            "! No-NAT: VPN clients to Clinical VLAN",
            "nat (outside,inside) source static OBJ-VPN-POOL OBJ-VPN-POOL "
            "destination static OBJ-INSIDE-NET OBJ-INSIDE-NET no-proxy-arp route-lookup",
        ]

    def acls(self) -> list[str]:
        c = self.cfg
        inside_net = ".".join(c.inside_ip.split(".")[:3]) + ".0"
        dmz_net = ".".join(c.dmz_ip.split(".")[:3]) + ".0"
        vpn_net = ".".join(c.vpn_pool_start.split(".")[:3]) + ".0"
        return [
            # Outside inbound
            "access-list ACL-OUTSIDE-IN extended permit tcp any host "
            f"{c.outside_ip} eq 443",
            "access-list ACL-OUTSIDE-IN extended permit udp any host "
            f"{c.outside_ip} eq 443",
            "access-list ACL-OUTSIDE-IN extended deny ip any any log",
            # Inside outbound
            f"access-list ACL-INSIDE-OUT extended permit tcp {inside_net} {c.inside_mask} any eq 443",
            f"access-list ACL-INSIDE-OUT extended permit tcp {inside_net} {c.inside_mask} any eq 80",
            f"access-list ACL-INSIDE-OUT extended permit udp {inside_net} {c.inside_mask} any eq 53",
            f"access-list ACL-INSIDE-OUT extended permit tcp {inside_net} {c.inside_mask} any eq 53",
            f"access-list ACL-INSIDE-OUT extended permit udp {inside_net} {c.inside_mask} any eq 123",
            f"access-list ACL-INSIDE-OUT extended deny ip {inside_net} {c.inside_mask} "
            f"{dmz_net} {c.dmz_mask} log",
            "access-list ACL-INSIDE-OUT extended deny ip any any log",
            # VPN to Clinical
            f"access-list ACL-VPN-TO-INSIDE extended permit tcp {vpn_net} {c.vpn_pool_mask} "
            f"{inside_net} {c.inside_mask} eq 3389",
            f"access-list ACL-VPN-TO-INSIDE extended permit tcp {vpn_net} {c.vpn_pool_mask} "
            f"{inside_net} {c.inside_mask} eq 443",
            f"access-list ACL-VPN-TO-INSIDE extended permit icmp {vpn_net} {c.vpn_pool_mask} "
            f"{inside_net} {c.inside_mask}",
            "access-list ACL-VPN-TO-INSIDE extended deny ip any any log",
            # DMZ to Clinical (management access)
            f"access-list ACL-DMZ-TO-INSIDE extended permit tcp {dmz_net} {c.dmz_mask} "
            f"{inside_net} {c.inside_mask} eq 3389",
            f"access-list ACL-DMZ-TO-INSIDE extended permit tcp {dmz_net} {c.dmz_mask} "
            f"{inside_net} {c.inside_mask} eq 5985",
            f"access-list ACL-DMZ-TO-INSIDE extended permit icmp {dmz_net} {c.dmz_mask} "
            f"{inside_net} {c.inside_mask}",
            "access-list ACL-DMZ-TO-INSIDE extended deny ip any any log",
            # VPN split tunnel
            f"access-list ACL-VPN-SPLIT standard permit {inside_net} {c.inside_mask}",
            f"access-list ACL-VPN-SPLIT standard permit {dmz_net} {c.dmz_mask}",
            # Apply to interfaces
            "access-group ACL-OUTSIDE-IN  in interface outside",
            "access-group ACL-INSIDE-OUT  in interface inside",
            "access-group ACL-DMZ-TO-INSIDE in interface dmz",
        ]

    def tls_crypto(self) -> list[str]:
        c = self.cfg
        return [
            "ssl server-version tlsv1.2",
            "ssl cipher tlsv1.2 high",
            "ssl dh-group group14",
            f"ssl trust-point SELF-SIGNED outside",
            f"crypto key generate rsa label {c.rsa_label} modulus {c.rsa_modulus}",
            "crypto ca trustpoint SELF-SIGNED",
            " enrollment self",
            f" subject-name CN={c.asa_public_fqdn},OU=IT,O=Clinical,C=GB",
            f" keypair {c.rsa_label}",
            f" fqdn {c.asa_public_fqdn}",
            "crypto ca enroll SELF-SIGNED noconfirm",
        ]

    def anyconnect_webvpn(self) -> list[str]:
        return [
            "crypto ikev2 enable outside",
            "webvpn",
            " enable outside",
            " anyconnect enable",
            " anyconnect image disk0:/anyconnect-win-webdeploy-k9.pkg 1",
            " anyconnect profiles CLINICAL-VPN-PROFILE disk0:/clinical_vpn_profile.xml",
            " cache disable",
            " error-recovery disable",
        ]

    def saml_config(self) -> list[str]:
        c = self.cfg
        if not c.tenant_id:
            log.warning("tenant_id not set – SAML commands will use placeholder <TENANT_ID>")
            tid = "<TENANT_ID>"
        else:
            tid = c.tenant_id
        return [
            f"saml idp https://sts.windows.net/{tid}/",
            f" url sign-in  https://login.microsoftonline.com/{tid}/saml2",
            f" url sign-out https://login.microsoftonline.com/{tid}/saml2",
            f" base-url     https://{c.outside_ip}",
            " trustpoint idp ENTRA-ID-CERT",
            " trustpoint sp  SELF-SIGNED",
            " no signature",
            " timeout assertion 1800",
        ]

    def group_policy(self) -> list[str]:
        c = self.cfg
        return [
            "group-policy GP-CLINICAL-VPN internal",
            "group-policy GP-CLINICAL-VPN attributes",
            " vpn-tunnel-protocol ssl-client",
            " split-tunnel-policy tunnelspecified",
            " split-tunnel-network-list value ACL-VPN-SPLIT",
            f" dns-server value {c.vpn_dns}",
            " wins-server none",
            f" default-domain value {c.domain_name}",
            " vpn-session-timeout 480",
            " vpn-idle-timeout 30",
            " anyconnect profiles value CLINICAL-VPN-PROFILE type user",
            " webvpn",
            "  anyconnect keep-installer installed",
            "  anyconnect dtls enable",
            "  anyconnect mtu 1400",
        ]

    def tunnel_group(self) -> list[str]:
        c = self.cfg
        if not c.tenant_id:
            tid = "<TENANT_ID>"
        else:
            tid = c.tenant_id
        return [
            f"tunnel-group {c.vpn_tunnel_group} type remote-access",
            f"tunnel-group {c.vpn_tunnel_group} general-attributes",
            " address-pool VPN-ADDRESS-POOL",
            " default-group-policy GP-CLINICAL-VPN",
            " authorization-required",
            f"tunnel-group {c.vpn_tunnel_group} webvpn-attributes",
            " authentication saml",
            f" saml identity-provider https://sts.windows.net/{tid}/",
            f" group-alias {c.vpn_group_alias} enable",
            " without-csd",
            f"ip local pool VPN-ADDRESS-POOL {c.vpn_pool_start}-{c.vpn_pool_end} "
            f"mask {c.vpn_pool_mask}",
        ]

    def syslog(self) -> list[str]:
        c = self.cfg
        return [
            "logging enable",
            "logging timestamp",
            "logging buffered informational",
            f"logging host inside {c.syslog_server} udp/514",
            "logging trap informational",
            "logging facility 20",
            "logging device-id hostname",
        ]

    def snmp(self) -> list[str]:
        c = self.cfg
        return [
            f"snmp-server community {c.snmp_community} ro",
            f"snmp-server location UK-Clinical-POC-DC1",
            f"snmp-server host inside {c.snmp_server} community {c.snmp_community}",
        ]

    def ssh_management(self) -> list[str]:
        c = self.cfg
        dmz_net = ".".join(c.dmz_ip.split(".")[:3]) + ".0"
        vpn_net = ".".join(c.vpn_pool_start.split(".")[:3]) + ".0"
        return [
            f"ssh {dmz_net} {c.dmz_mask} dmz",
            f"ssh {vpn_net} {c.vpn_pool_mask} outside",
            "ssh timeout 10",
            "ssh version 2",
            "ssh key-exchange group dh-group14-sha256",
            "aaa authentication ssh console LOCAL",
        ]

    def threat_detection(self) -> list[str]:
        return [
            "threat-detection basic-threat",
            "threat-detection statistics",
            "threat-detection statistics tcp-intercept rate-interval 30 "
            "burst-rate 400 average-rate 200",
        ]

    def ntp(self) -> list[str]:
        return [
            "ntp server 169.254.169.123",  # Azure NTP
            "ntp authenticate",
        ]

    def all_steps(self) -> dict[str, list[str]]:
        """Return ordered dict of step_name -> commands."""
        return {
            "global": self.global_settings(),
            "interfaces": self.interfaces(),
            "routing": self.routing(),
            "objects": self.network_objects(),
            "nat": self.nat_rules(),
            "acl": self.acls(),
            "tls": self.tls_crypto(),
            "webvpn": self.anyconnect_webvpn(),
            "saml": self.saml_config(),
            "group_policy": self.group_policy(),
            "tunnel_group": self.tunnel_group(),
            "syslog": self.syslog(),
            "snmp": self.snmp(),
            "ssh": self.ssh_management(),
            "threat": self.threat_detection(),
            "ntp": self.ntp(),
        }


# ---------------------------------------------------------------------------
# Deployer
# ---------------------------------------------------------------------------

class ASADeployer:
    """Connects to the ASA and applies configuration."""

    def __init__(self, cfg: ASAConfig, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.conn: Optional[ConnectHandler] = None
        self.builder = ASACommandBuilder(cfg)
        self.results: dict[str, bool] = {}

    def connect(self) -> None:
        if self.dry_run:
            log.info("[DRY-RUN] Skipping connection to %s", self.cfg.host)
            return
        log.info("Connecting to ASA at %s:%s …", self.cfg.host, self.cfg.port)
        device = {
            "device_type": "cisco_asa",
            "host": self.cfg.host,
            "username": self.cfg.username,
            "password": self.cfg.password,
            "secret": self.cfg.enable_password,
            "port": self.cfg.port,
            "timeout": 60,
            "session_timeout": 120,
            "banner_timeout": 30,
            "conn_timeout": 30,
            "fast_cli": False,
        }
        try:
            self.conn = ConnectHandler(**device)
            self.conn.enable()
            log.info("Connected and enabled. Prompt: %s", self.conn.find_prompt())
        except NetmikoAuthenticationException as e:
            log.error("Authentication failed: %s", e)
            raise
        except NetmikoTimeoutException as e:
            log.error("Connection timed out: %s", e)
            raise

    def disconnect(self) -> None:
        if self.conn:
            self.conn.disconnect()
            log.info("Disconnected from ASA.")

    def _send_commands(self, step_name: str, commands: list[str]) -> bool:
        """Send a list of commands as a configuration block."""
        # Filter comment-only lines for actual sending
        config_cmds = [c for c in commands if not c.strip().startswith("!")]

        log.info("--- Step: %s (%d commands) ---", step_name, len(config_cmds))
        for cmd in commands:
            log.debug("  CMD: %s", cmd)

        if self.dry_run:
            log.info("[DRY-RUN] Would send %d config commands for step '%s'",
                     len(config_cmds), step_name)
            self.results[step_name] = True
            return True

        if not self.conn:
            raise RuntimeError("Not connected to ASA")

        try:
            output = self.conn.send_config_set(
                config_cmds,
                cmd_verify=False,
                delay_factor=2,
            )
            if "ERROR" in output.upper() or "INVALID INPUT" in output.upper():
                log.error("Step '%s' encountered errors:\n%s", step_name, output)
                self.results[step_name] = False
                return False
            log.info("Step '%s' completed successfully.", step_name)
            self.results[step_name] = True
            return True
        except NetmikoBaseException as e:
            log.error("Step '%s' failed: %s", step_name, e)
            self.results[step_name] = False
            return False

    def backup_config(self, output_path: str = "asa_backup.txt") -> None:
        """Save current running config to a local file."""
        if self.dry_run:
            log.info("[DRY-RUN] Would backup running-config to %s", output_path)
            return
        log.info("Backing up running-config to %s …", output_path)
        output = self.conn.send_command("show running-config", read_timeout=90)
        Path(output_path).write_text(output)
        log.info("Backup saved: %s (%d bytes)", output_path, len(output))

    def verify_interfaces(self) -> bool:
        """Verify interface configuration post-deployment."""
        if self.dry_run:
            log.info("[DRY-RUN] Would verify interfaces")
            return True
        output = self.conn.send_command("show interface ip brief")
        log.info("Interface status:\n%s", output)
        required = [self.cfg.outside_ip, self.cfg.inside_ip, self.cfg.dmz_ip]
        for ip in required:
            if ip not in output:
                log.error("Expected IP %s not found in interface output", ip)
                return False
        log.info("Interface verification PASSED.")
        return True

    def verify_vpn(self) -> bool:
        """Verify AnyConnect / VPN configuration."""
        if self.dry_run:
            log.info("[DRY-RUN] Would verify VPN configuration")
            return True
        output = self.conn.send_command("show webvpn group-policy")
        log.info("WebVPN group policy:\n%s", output)
        return "GP-CLINICAL-VPN" in output

    def verify_acls(self) -> bool:
        """Verify ACL entries are present."""
        if self.dry_run:
            log.info("[DRY-RUN] Would verify ACLs")
            return True
        output = self.conn.send_command("show access-list | include ACL-")
        log.info("ACL summary:\n%s", output)
        required = ["ACL-OUTSIDE-IN", "ACL-INSIDE-OUT", "ACL-VPN-TO-INSIDE"]
        return all(acl in output for acl in required)

    def save_config(self) -> None:
        """Write memory on ASA."""
        if self.dry_run:
            log.info("[DRY-RUN] Would write memory")
            return
        log.info("Saving configuration (write memory) …")
        output = self.conn.send_command("write memory", read_timeout=30)
        log.info("Save output: %s", output.strip())

    def run(self, steps_filter: Optional[list[str]] = None) -> bool:
        """Execute all (or filtered) deployment steps."""
        all_steps = self.builder.all_steps()

        if steps_filter:
            steps = {k: v for k, v in all_steps.items() if k in steps_filter}
            if not steps:
                log.error("No matching steps found. Available: %s", list(all_steps.keys()))
                return False
        else:
            steps = all_steps

        log.info("=== ASA Deployment Started ===")
        log.info("Target: %s | Steps: %s | Dry-run: %s",
                 self.cfg.host, list(steps.keys()), self.dry_run)

        self.connect()

        if not self.dry_run:
            self.backup_config(f"asa_backup_pre_{int(time.time())}.txt")

        overall_success = True
        for step_name, commands in steps.items():
            ok = self._send_commands(step_name, commands)
            if not ok:
                overall_success = False
                log.error("Step '%s' failed – continuing with remaining steps.", step_name)

        # Verification phase
        log.info("=== Verification Phase ===")
        if not self.verify_interfaces():
            overall_success = False
        if not self.verify_acls():
            overall_success = False
        if not self.verify_vpn():
            overall_success = False

        if overall_success and not self.dry_run:
            self.save_config()

        self.disconnect()

        # Summary
        log.info("=== Deployment Summary ===")
        for step, passed in self.results.items():
            status = "PASS" if passed else "FAIL"
            log.info("  [%s] %s", status, step)

        overall_label = "SUCCESS" if overall_success else "FAILED"
        log.info("=== Overall Result: %s ===", overall_label)
        return overall_success

    def generate_config_file(self, output_path: str = "generated_asa_config.txt") -> None:
        """Write the full configuration to a local file (no device connection needed)."""
        all_steps = self.builder.all_steps()
        lines = [
            "! ============================================================",
            f"! Cisco ASA Configuration – Azure Local POC Clinical",
            f"! Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
            "! ============================================================",
            "",
        ]
        for step_name, commands in all_steps.items():
            lines.append(f"! --- {step_name.upper()} ---")
            lines.extend(commands)
            lines.append("")

        lines.append("write memory")
        content = "\n".join(lines)
        Path(output_path).write_text(content)
        log.info("Configuration file written to: %s", output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cisco ASA Automated Deployment – Azure Local POC",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (generate config, no device connection)
  python asa_deploy.py --config config.yml --dry-run

  # Deploy all steps
  python asa_deploy.py --config config.yml

  # Deploy only interfaces and ACL steps
  python asa_deploy.py --config config.yml --step interfaces --step acl

  # Generate config file without connecting
  python asa_deploy.py --config config.yml --generate-only

Available steps:
  global, interfaces, routing, objects, nat, acl, tls,
  webvpn, saml, group_policy, tunnel_group, syslog, snmp,
  ssh, threat, ntp
        """,
    )
    parser.add_argument("--config", required=True, help="Path to config.yml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without connecting to ASA")
    parser.add_argument("--step", action="append", dest="steps",
                        metavar="STEP_NAME",
                        help="Run only specific steps (repeatable)")
    parser.add_argument("--generate-only", action="store_true",
                        help="Write config to file without connecting to ASA")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.debug:
        log.setLevel(logging.DEBUG)

    # Prompt for credentials securely
    print(f"ASA Deployment – config: {args.config}")
    password = getpass.getpass("ASA SSH password: ")
    enable_password = getpass.getpass("ASA enable password: ")

    try:
        cfg = ASAConfig.from_yaml(args.config, password, enable_password)
    except FileNotFoundError:
        log.error("Config file not found: %s", args.config)
        return 1
    except KeyError as e:
        log.error("Missing required key in config.yml: %s", e)
        return 1

    deployer = ASADeployer(cfg, dry_run=args.dry_run)

    if args.generate_only:
        deployer.generate_config_file()
        return 0

    success = deployer.run(steps_filter=args.steps)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
