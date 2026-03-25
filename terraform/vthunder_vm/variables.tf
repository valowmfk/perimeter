variable "proxmox_endpoint" {
  description = "Proxmox API endpoint (e.g. https://proxmox:8006/)"
  type        = string
  default     = "https://proxmox:8006/"
}

# Optional if you want to pass token via TF instead of env
variable "proxmox_api_token" {
  description = "Proxmox API token in format user@realm!tokenid=secret"
  type        = string
  default     = ""
  sensitive   = true
}

variable "vthunder_configs" {
  description = "vThunder VM definitions (per VMID)"
  type = map(object({
    vm_id        = number
    name         = string
    node         = string
    template_id  = number       # VMID of acos template (9005/9006/9007)
    datastore_id = string       # e.g. \"zfs-pool\"

    memory  = number          # MB
    cores   = number          # vCPUs
    bridges = list(string)    # NICs: first is mgmt, rest are data plane

    # Metadata fields for Python bootstrap scripts (not used by Terraform)
    ipv4_address = string           # Static mgmt IP (e.g., "10.1.55.49/24")
    ipv4_gateway = string           # Gateway (e.g., "10.1.55.254")
    dns_servers  = list(string)     # DNS servers
    ssh_username = string           # SSH user for ACOS
    ssh_keys     = list(string)     # SSH keys (currently unused for vThunder)
    acos_version = string           # ACOS version tag (e.g., "6.0.2")
    tags         = optional(list(string))

    on_boot = optional(bool)
  }))
  default = {}
}