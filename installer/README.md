<p align="center">
  <img src="web/static/assets/perimeter/perimeter-logo.svg" alt="Perimeter" width="128" height="128">
</p>

<h1 align="center">Perimeter</h1>
<p align="center"><strong>Automation Platform for Homelab Infrastructure</strong></p>

<p align="center">
  Provision VMs, manage certificates, orchestrate Ansible playbooks, build SLB configurations on A10 vThunders, and monitor it all from a single web interface.
</p>

---

## What is Perimeter?

Perimeter is a self-hosted automation platform built for homelab and small infrastructure environments. It provides a unified web UI for:

- **VM Provisioning** — Clone Proxmox templates via Terraform with cloud-init. Supports Linux, VyOS, and A10 vThunder ACOS.
- **Ansible Orchestration** — Run playbooks against your inventory directly from the browser.
- **Certificate Management** — Issue and renew Let's Encrypt certificates via Certbot, deploy to Linux hosts or A10 vThunders.
- **DNS Management** — Create and manage local DNS records via Pi-hole v6 API.
- **IPAM** — Track IP address allocations in Netbox.
- **vThunder SLB Manager** — Build and tear down VIPs, service groups, health monitors, and SSL templates on A10 load balancers via aXAPI.
- **Template Refresh** — Automated template maintenance: clone, boot, update, clean, re-template.
- **Monitoring** — Prometheus `/metrics` endpoint with a pre-built Grafana dashboard.

Every feature is **modular** — enable only what you need via environment variables. No feature dependencies; disable DNS, IPAM, or vThunder management without affecting core VM operations.

## Screenshots

*Coming soon*

## Quick Start

### Prerequisites

- A **Proxmox VE** cluster (8.x or 9.x) with API access
- A **Rocky Linux 9**, **RHEL 9**, **Ubuntu 22.04+**, or **Debian 12+** server to run Perimeter
- VM templates in Proxmox with **cloud-init** and **QEMU guest agent** enabled

### Install

```bash
curl -fsSL https://raw.githubusercontent.com/valowmfk/perimeter/main/installer/install.sh | sudo bash
```

The installer will:
1. Install system dependencies (Python, Terraform, Ansible, SOPS, Age)
2. Clone the repository to `/opt/perimeter/`
3. Launch an interactive setup wizard

The setup wizard guides you through:
- Service user creation
- Feature selection (choose which modules to enable)
- Proxmox API token creation (automatic)
- Network/subnet configuration
- SSH key generation
- Credential collection for enabled features
- Secret encryption with SOPS + Age
- Systemd service installation

### Manual Install

```bash
git clone https://github.com/valowmfk/perimeter.git /opt/perimeter
cd /opt/perimeter
pip3 install -r requirements.txt
sudo python3 installer/setup.py --install-dir /opt/perimeter
```

## Architecture

```
Perimeter (Flask)
├── Web UI (single-page app)
│   ├── VM Provisioning drawer
│   ├── Ansible Orchestration drawer
│   ├── Certificate Management drawer
│   ├── DNS Management drawer
│   ├── IPAM drawer
│   └── vThunder SLB Manager drawer
│
├── Backend (Flask blueprints)
│   ├── core_bp — UI serving (always on)
│   ├── vms_bp — VM lifecycle (always on)
│   ├── network_bp — Subnet/DNS API (always on)
│   ├── playbooks_bp — Ansible execution (toggleable)
│   ├── certificates_bp — Cert management (toggleable)
│   └── system_bp — vThunder/Proxmox (toggleable)
│
├── Workflows (subprocess workers)
│   ├── provision_linux.py
│   ├── provision_vthunder.py
│   ├── provision_vyos.py
│   ├── bootstrap_linux.py
│   ├── bootstrap_vthunder.py
│   ├── destroy_vm.py
│   └── refresh_template.py
│
├── Terraform (IaC)
│   ├── modules/linux_vm
│   ├── modules/vthunder_vm
│   └── modules/vyos_vm
│
└── Integrations
    ├── Proxmox API (VM management)
    ├── A10 aXAPI v3 (vThunder configuration)
    ├── Pi-hole v6 API (DNS records)
    ├── Netbox API (IPAM tracking)
    ├── Loki (audit logging)
    └── Prometheus (metrics)
```

## Feature Toggles

Every optional module can be enabled or disabled via environment variables:

| Variable | Default | Module |
|----------|---------|--------|
| `PERIMETER_FEATURE_ANSIBLE` | `1` | Ansible Orchestration |
| `PERIMETER_FEATURE_CERTS` | `1` | Certificate Management |
| `PERIMETER_FEATURE_DNS` | `1` | DNS Management (Pi-hole) |
| `PERIMETER_FEATURE_IPAM` | `1` | IPAM (Netbox) |
| `PERIMETER_FEATURE_VTHUNDER` | `1` | vThunder SLB Manager |
| `PERIMETER_FEATURE_AUDIT` | `1` | Audit Logging (Loki) |

Set to `0` to disable. Features are set in the systemd service file or as environment variables.

When a feature is disabled:
- Its Flask blueprint is not registered (no API routes)
- Its UI drawer is not rendered (no HTML sent to browser)
- Its JavaScript module is not initialized
- Provision workflows skip DNS/IPAM steps gracefully

## Configuration

### Secrets

All sensitive configuration is encrypted with [SOPS](https://github.com/getsops/sops) + [Age](https://github.com/FiloSottile/age). The installer handles key generation and encryption automatically.

Secrets are stored in `secrets/automation-demo.enc.env` and decrypted at runtime by the application.

### Subnets

Multiple subnets are supported. Configure in `python/config.py`:

```python
SUBNETS = {
    "10.1.55.0/24": {"gateway": "10.1.55.254", "dns": ["10.1.55.10", "10.1.55.11"]},
    "10.255.0.0/24": {"gateway": "10.255.0.1", "dns": ["10.1.55.10", "10.1.55.11"]},
}
```

The UI provides a subnet dropdown during VM creation, with IP validation against the selected subnet.

### Proxmox Templates

Perimeter auto-discovers templates from Proxmox. Templates are routed by naming convention:

| Template prefix | Workflow | Resources |
|----------------|----------|-----------|
| `acos*` | vThunder | Template-defined (fixed) |
| `vyos*` | VyOS | Template-defined (fixed) |
| Everything else | Linux | User-configurable (CPU/RAM/Disk) |

Templates must have **cloud-init** and **QEMU guest agent** enabled.

## Monitoring

### Prometheus

Perimeter exposes a `/metrics` endpoint with:

- `perimeter_http_requests_total` — HTTP request count by method/endpoint/status
- `perimeter_http_request_duration_seconds` — Request duration histogram
- `perimeter_vm_provisions_total` — VM provisioning by type/status
- `perimeter_playbook_runs_total` — Playbook execution count
- `perimeter_cert_deploys_total` — Certificate deployments
- `perimeter_vip_operations_total` — VIP create/destroy operations
- `perimeter_template_refreshes_total` — Template refresh operations

Add to your Prometheus scrape config:

```yaml
- job_name: 'perimeter'
  static_configs:
    - targets: ['<perimeter-ip>:8080']
```

### Grafana

A pre-built dashboard is available at `grafana/perimeter-dashboard.json`. Import via Grafana UI → Dashboards → Import.

## Service Management

```bash
# View logs
journalctl -u perimeter -f

# Restart
sudo systemctl restart perimeter

# Status
sudo systemctl status perimeter

# Edit feature toggles
sudo systemctl edit perimeter
# Add: Environment=PERIMETER_FEATURE_IPAM=0
sudo systemctl restart perimeter
```

## Development

```bash
# Run locally (development mode)
cd /opt/perimeter
python3 qbranch_app.py

# Run tests
pytest tests/
```

## Tech Stack

- **Backend**: Python 3.9+, Flask
- **Frontend**: Vanilla JavaScript (ES modules), CSS
- **IaC**: Terraform (BPG Proxmox provider)
- **Config Management**: Ansible
- **Secrets**: SOPS + Age encryption
- **Monitoring**: Prometheus + Grafana
- **Audit**: Loki
- **Service**: systemd

## License

*TBD*

## Contributing

*TBD*
