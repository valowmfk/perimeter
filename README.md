# Q Branch Automation Platform

Homelab automation platform for provisioning and managing virtual machines on Proxmox VE, with integrated DNS, IPAM, certificate management, and configuration management.

## Architecture

```
Flask Web UI (qbranch_app.py)
    ├── routes/           # 6 Flask blueprints (vms, playbooks, certificates, dns, system, network)
    ├── python/
    │   ├── workflows/    # Provisioning orchestration (Linux VMs, A10 vThunders)
    │   ├── helpers/      # DNS (Pi-hole v6), IPAM (Netbox), cert upload (aXAPI)
    │   ├── utils/        # SOPS env loader, tfvars I/O, Terraform runner
    │   └── axapi/        # A10 ACOS aXAPI client library
    ├── terraform/        # IaC modules (linux_vm, vthunder_vm, vyos_vm)
    ├── playbooks/        # Ansible playbooks (bootstrap, cert deploy, SSH keys)
    └── web/              # Frontend (ES modules, dark theme UI)
```

## What It Does

- **VM Provisioning**: Create Linux VMs and A10 vThunder appliances on Proxmox via Terraform, with automatic DNS registration (Pi-hole), IPAM tracking (Netbox), and Ansible bootstrap
- **VM Lifecycle**: Protect, re-bootstrap, destroy VMs with full cleanup (Terraform destroy + DNS/IPAM/inventory removal)
- **Certificate Management**: Issue, deploy, and manage TLS certificates via Certbot, with automated deployment to vThunder load balancers
- **Ansible Orchestration**: Run playbooks against inventory groups from the web UI with streaming terminal output
- **DNS Management**: Create/delete Pi-hole DNS records with nebula-sync replication
- **IPAM**: Query Netbox for available IPs, auto-register on provision, auto-remove on destroy

## Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | Flask 3.x with Blueprints |
| Frontend | Vanilla JS (ES modules), CSS custom properties |
| IaC | Terraform (BPG Proxmox provider) |
| Config Mgmt | Ansible |
| Secrets | SOPS + Age encryption |
| DNS | Pi-hole v6 REST API |
| IPAM | Netbox REST API |
| Hypervisor | Proxmox VE |
| Auth | Traefik ForwardAuth (edge) + audit logging (Loki) |

## Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Decrypt secrets (requires Age key at ~/.config/sops/age/keys.txt)
sops -d secrets/automation-demo.enc.env

# Initialize Terraform providers
cd terraform/linux_vm && terraform init
cd terraform/vthunder_vm && terraform init

# Run the application
python qbranch_app.py
```

The app runs on port 5000 by default, managed via systemd in production.

## Testing

```bash
# Run all tests
python -m pytest

# With coverage
python -m pytest --cov=python --cov=routes --cov-report=term-missing
```

## Project Structure

```
automation-demo/
├── qbranch_app.py              # Flask app factory + entry point
├── python/
│   ├── config.py               # Centralized config dataclass
│   ├── axapi/                   # A10 aXAPI client (context manager pattern)
│   ├── helpers/
│   │   ├── dns_manager.py      # Pi-hole v6 DNS CRUD
│   │   ├── netbox_ipam.py      # Netbox IP management
│   │   └── upload_cert_to_vthunder.py
│   ├── utils/
│   │   ├── sops_env.py         # SOPS-encrypted env file loader
│   │   ├── tfvars_io.py        # Atomic tfvars read/write with file locking
│   │   ├── terraform_runner.py # Terraform init/apply/output helpers
│   │   └── vm_track.py         # VM cleanup state tracking
│   └── workflows/
│       ├── provision_linux.py   # Linux VM provisioning orchestration
│       ├── provision_vthunder.py # vThunder provisioning orchestration
│       ├── destroy_vm.py        # Full VM teardown chain
│       ├── bootstrap_linux.py   # SSH wait + Ansible bootstrap
│       ├── bootstrap_vthunder.py # aXAPI bootstrap (password, syslog, SSH key)
│       └── dhcp_scanner.py      # DHCP IP discovery for vThunders
├── routes/
│   ├── shared.py               # Validation, helpers, shared state
│   ├── vms_bp.py               # VM CRUD + protection + cleanup
│   ├── playbooks_bp.py         # Ansible playbook execution
│   ├── certificates_bp.py      # Certificate management
│   ├── network_bp.py           # DNS + IPAM
│   ├── system_bp.py            # Health, bridges, templates
│   ├── core_bp.py              # Static file serving
│   └── audit.py                # Audit logging to Loki
├── terraform/
│   ├── linux_vm/               # Linux VM workspace
│   ├── vthunder_vm/            # vThunder workspace
│   └── modules/                # Reusable TF modules
├── playbooks/                  # Ansible playbooks
├── inventories/                # Ansible inventory files
├── secrets/                    # SOPS-encrypted credentials
├── web/
│   ├── static/
│   │   ├── js/
│   │   │   ├── app.js          # Entry point + event delegation
│   │   │   ├── modules/        # Feature modules (vms, ansible, certs, dns, ipam, telemetry, themes)
│   │   │   └── utils/          # Shared utilities (dom, ansi, modal, busy)
│   │   └── style.css
│   └── templates/
│       └── index.html
├── tests/                      # pytest test suite
├── scripts/                    # Operational scripts
└── .github/workflows/ci.yml   # GitHub Actions (test + lint)
```
