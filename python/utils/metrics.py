"""Prometheus metrics for Perimeter Automation Platform."""

from prometheus_client import Counter, Histogram, Gauge

# ── HTTP Request Metrics ──────────────────────────────────────
HTTP_REQUESTS = Counter(
    "perimeter_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
HTTP_DURATION = Histogram(
    "perimeter_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ── VM Provisioning ───────────────────────────────────────────
VM_PROVISIONS = Counter(
    "perimeter_vm_provisions_total",
    "Total VM provisioning operations",
    ["vm_type", "status"],
)
VM_PROVISIONS_ACTIVE = Gauge(
    "perimeter_vm_provisions_active",
    "Currently running VM provisions",
)

# ── Playbook Runs ─────────────────────────────────────────────
PLAYBOOK_RUNS = Counter(
    "perimeter_playbook_runs_total",
    "Total Ansible playbook runs",
    ["playbook", "status"],
)
PLAYBOOK_RUNS_ACTIVE = Gauge(
    "perimeter_playbook_runs_active",
    "Currently running playbooks",
)

# ── Certificate Deployments ───────────────────────────────────
CERT_DEPLOYS = Counter(
    "perimeter_cert_deploys_total",
    "Total certificate deployments",
    ["status"],
)

# ── VIP Operations ────────────────────────────────────────────
VIP_OPERATIONS = Counter(
    "perimeter_vip_operations_total",
    "Total VIP create/destroy operations",
    ["action", "status"],
)

# ── Template Refreshes ────────────────────────────────────────
TEMPLATE_REFRESHES = Counter(
    "perimeter_template_refreshes_total",
    "Total template refresh operations",
    ["template", "status"],
)

# ── System Health ─────────────────────────────────────────────
PROXMOX_UP = Gauge(
    "perimeter_proxmox_up",
    "Proxmox cluster reachability (1=up, 0=down)",
)
PIHOLE_UP = Gauge(
    "perimeter_pihole_up",
    "Pi-hole reachability (1=up, 0=down)",
)
