"""
SLB configuration manager for A10 vThunder devices.

Creates and destroys full SLB stacks (servers, health monitors,
service groups, SSL templates, virtual servers) via aXAPI v3.

Replaces the hardcoded 00-a10-build.yml and 00-a10-destroy.yml playbooks.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from axapi.client import AxapiClient
from axapi.errors import AxapiError
from axapi.utils import qlog, qlog_success, qlog_error, qlog_warning

COMPONENT = "SLB-MGR"


def create_slb_config(host, username, password, port, partition, config):
    """
    Create a full SLB stack on a vThunder device.

    config dict:
        vip_name:           str  — virtual server name
        vip_ip:             str  — VIP IP address
        cert_name:          str  — certificate name on device (for client-ssl template)
        http_redirect:      bool — port 80 → 443 redirect (default True)
        service_group_name: str  — service group name (auto-derived if empty)
        health_monitor:     str  — health monitor name (optional)
        backends:           list — [{"name": str, "ip": str, "port": int}, ...]

    Returns dict with step-by-step results.
    """
    vip_name = config["vip_name"]
    vip_ip = config["vip_ip"]
    cert_name = config.get("cert_name", "")
    http_redirect = config.get("http_redirect", True)
    sg_name = config.get("service_group_name") or f"{vip_name.lower().replace('-', '_')}_sg"
    hm_config = config.get("health_monitor")  # dict or None
    hm_name = hm_config.get("name", "") if isinstance(hm_config, dict) else ""
    backends = config.get("backends", [])

    ssl_template_name = f"{cert_name}_client_ssl" if cert_name else ""

    results = {"steps": [], "success": True}

    def log_step(name, success, detail=""):
        results["steps"].append({"name": name, "success": success, "detail": detail})
        if success:
            qlog_success(COMPONENT, f"{name}: {detail}" if detail else name)
        else:
            qlog_error(COMPONENT, f"{name} FAILED: {detail}" if detail else f"{name} FAILED")
            results["success"] = False

    with AxapiClient(host=host, username=username, password=password, port=port, component=COMPONENT) as client:
        client.login()

        # Switch partition
        if partition and partition != "shared":
            client.post(f"/axapi/v3/active-partition/{partition}", body={})
            qlog(COMPONENT, f"Switched to partition: {partition}")

        # 1. Create backend servers with port definitions
        for backend in backends:
            try:
                protocol = backend.get("protocol", "tcp")
                port = backend["port"]
                server_body = {
                    "server": {
                        "name": backend["name"],
                        "host": backend["ip"],
                        "port-list": [{
                            "port-number": port,
                            "protocol": protocol,
                        }],
                    }
                }
                if backend.get("health_check_disable"):
                    server_body["server"]["health-check-disable"] = 1
                    server_body["server"]["port-list"][0]["health-check-disable"] = 1
                client.post("/axapi/v3/slb/server", body=server_body)
                log_step(f"Create server '{backend['name']}'", True, f"{backend['ip']}:{port}/{protocol}")
            except AxapiError as e:
                log_step(f"Create server '{backend['name']}'", False, str(e))
                return results

        # 2. Create health monitor (if specified)
        if hm_name and isinstance(hm_config, dict):
            try:
                client.post("/axapi/v3/health/monitor", body={
                    "monitor": {
                        "name": hm_name,
                        "retry": hm_config.get("retry", 2),
                        "up-retry": hm_config.get("up_retry", 4),
                        "interval": hm_config.get("interval", 5),
                        "timeout": hm_config.get("timeout", 1),
                    }
                })
                log_step(f"Create health monitor '{hm_name}'", True)

                # Add HTTP method to health monitor
                http_method = {
                    "http": 1,
                    "http-port": hm_config.get("http_port", 80),
                    "http-url": 1,
                    "url-type": "GET",
                    "url-path": hm_config.get("url_path", "/"),
                }
                expect_str = hm_config.get("expect", "")
                if expect_str:
                    http_method["http-expect"] = 1
                    http_method["http-text"] = expect_str
                client.post(f"/axapi/v3/health/monitor/{hm_name}/method/http", body={
                    "http": http_method
                })
                log_step(f"Configure HTTP check on '{hm_name}'", True,
                         f"port={hm_config.get('http_port', 80)} path={hm_config.get('url_path', '/')}")
            except AxapiError as e:
                log_step(f"Create health monitor '{hm_name}'", False, str(e))
                return results

        # 3. Create service group with members
        try:
            # Use protocol from first backend, default to tcp
            sg_protocol = backends[0].get("protocol", "tcp") if backends else "tcp"
            member_list = []
            for b in backends:
                member = {"name": b["name"], "port": b["port"]}
                if b.get("health_check_disable"):
                    member["member-stats-data-disable"] = 1
                member_list.append(member)
            sg_body = {
                "service-group": {
                    "name": sg_name,
                    "protocol": sg_protocol,
                    "member-list": member_list,
                }
            }
            if hm_name:
                sg_body["service-group"]["health-check"] = hm_name
            client.post("/axapi/v3/slb/service-group", body=sg_body)
            log_step(f"Create service group '{sg_name}'", True, f"{len(backends)} members")
        except AxapiError as e:
            log_step(f"Create service group '{sg_name}'", False, str(e))
            return results

        # 4. Create client-SSL template (if cert provided)
        if cert_name and ssl_template_name:
            try:
                client.post("/axapi/v3/slb/template/client-ssl", body={
                    "client-ssl": {
                        "name": ssl_template_name,
                        "cert": cert_name,
                        "key": cert_name,
                    }
                })
                log_step(f"Create SSL template '{ssl_template_name}'", True, f"cert={cert_name}")
            except AxapiError as e:
                log_step(f"Create SSL template '{ssl_template_name}'", False, str(e))
                return results

        # 5. Create virtual server
        try:
            port_list = []

            if http_redirect:
                port_list.append({
                    "port-number": 80,
                    "protocol": "http",
                    "service-group": sg_name,
                    "auto": 1,
                    "action": "enable",
                    "redirect-to-https": 1,
                })

            https_port = {
                "port-number": 443,
                "protocol": "https",
                "service-group": sg_name,
                "auto": 1,
                "action": "enable",
            }
            if ssl_template_name:
                https_port["template-client-ssl"] = ssl_template_name
            port_list.append(https_port)

            client.post("/axapi/v3/slb/virtual-server", body={
                "virtual-server": {
                    "name": vip_name,
                    "ip-address": vip_ip,
                    "port-list": port_list,
                }
            })
            log_step(f"Create virtual server '{vip_name}'", True, f"IP={vip_ip}")
        except AxapiError as e:
            log_step(f"Create virtual server '{vip_name}'", False, str(e))
            return results

        # Write memory to persist
        try:
            client.write_memory()
            log_step("Write memory", True)
        except AxapiError as e:
            log_step("Write memory", False, str(e))

    return results


def destroy_slb_config(host, username, password, port, partition, vip_name, cleanup_servers=True):
    """
    Tear down an SLB stack in reverse order.

    Queries the VIP to discover associated resources (service group, SSL template,
    servers) and removes them all.

    Args:
        vip_name:        Name of the virtual server to destroy
        cleanup_servers: If True, also delete backend servers from the service group

    Returns dict with step-by-step results.
    """
    results = {"steps": [], "success": True}

    def log_step(name, success, detail=""):
        results["steps"].append({"name": name, "success": success, "detail": detail})
        if success:
            qlog_success(COMPONENT, f"{name}: {detail}" if detail else name)
        else:
            qlog_error(COMPONENT, f"{name} FAILED: {detail}" if detail else f"{name} FAILED")
            results["success"] = False

    with AxapiClient(host=host, username=username, password=password, port=port, component=COMPONENT) as client:
        client.login()

        # Switch partition
        if partition and partition != "shared":
            client.post(f"/axapi/v3/active-partition/{partition}", body={})
            qlog(COMPONENT, f"Switched to partition: {partition}")

        # Discover VIP details first
        try:
            vip_data = client.get(f"/axapi/v3/slb/virtual-server/{vip_name}")
            vip = vip_data.get("virtual-server", {})
        except AxapiError as e:
            log_step(f"Lookup VIP '{vip_name}'", False, str(e))
            return results

        # Extract associated resources from VIP
        port_list = vip.get("port-list", [])
        sg_names = set()
        ssl_template_names = set()
        for p in port_list:
            sg = p.get("service-group")
            if sg:
                sg_names.add(sg)
            tpl = p.get("template-client-ssl")
            if tpl:
                ssl_template_names.add(tpl)

        # Discover servers from service groups
        server_names = set()
        hm_names = set()
        for sg_name in sg_names:
            try:
                sg_data = client.get(f"/axapi/v3/slb/service-group/{sg_name}")
                sg = sg_data.get("service-group", {})
                for member in sg.get("member-list", []):
                    server_names.add(member["name"])
                hm = sg.get("health-check")
                if hm:
                    hm_names.add(hm)
            except AxapiError:
                pass

        qlog(COMPONENT, f"VIP '{vip_name}' uses: SG={sg_names}, SSL={ssl_template_names}, "
                         f"servers={server_names}, HM={hm_names}")

        # 1. Delete virtual server
        try:
            client.delete(f"/axapi/v3/slb/virtual-server/{vip_name}")
            log_step(f"Delete virtual server '{vip_name}'", True)
        except AxapiError as e:
            log_step(f"Delete virtual server '{vip_name}'", False, str(e))
            return results

        # 2. Delete SSL templates
        for tpl_name in ssl_template_names:
            try:
                client.delete(f"/axapi/v3/slb/template/client-ssl/{tpl_name}")
                log_step(f"Delete SSL template '{tpl_name}'", True)
            except AxapiError as e:
                log_step(f"Delete SSL template '{tpl_name}'", False, str(e))

        # 3. Delete service groups
        for sg_name in sg_names:
            try:
                client.delete(f"/axapi/v3/slb/service-group/{sg_name}")
                log_step(f"Delete service group '{sg_name}'", True)
            except AxapiError as e:
                log_step(f"Delete service group '{sg_name}'", False, str(e))

        # 4. Delete health monitors
        for hm_name in hm_names:
            try:
                client.delete(f"/axapi/v3/health/monitor/{hm_name}")
                log_step(f"Delete health monitor '{hm_name}'", True)
            except AxapiError as e:
                log_step(f"Delete health monitor '{hm_name}'", False, str(e))

        # 5. Delete backend servers (if cleanup requested)
        if cleanup_servers:
            for srv_name in server_names:
                try:
                    client.delete(f"/axapi/v3/slb/server/{srv_name}")
                    log_step(f"Delete server '{srv_name}'", True)
                except AxapiError as e:
                    log_step(f"Delete server '{srv_name}'", False, str(e))

        # Write memory to persist
        try:
            client.write_memory()
            log_step("Write memory", True)
        except AxapiError as e:
            log_step("Write memory", False, str(e))

    return results
