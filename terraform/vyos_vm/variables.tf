variable "proxmox_endpoint" {
  description = "Proxmox API endpoint (e.g. https://proxmox:8006/)"
  type        = string
  default     = "https://proxmox:8006/"
}

variable "proxmox_api_token" {
  description = "Proxmox API token in format user@realm!tokenid=secret"
  type        = string
  default     = ""
  sensitive   = true
}

variable "vyos_configs" {
  description = "VyOS VM definitions (cloud-init; per VMID)"
  type = map(object({
    vm_id        = number
    name         = string
    node         = string
    template_id  = number
    datastore_id = string

    memory   = number
    cores    = number
    disk_size = number

    bridges      = list(string)

    ipv4_address = string
    ipv4_gateway = string
    dns_servers  = list(string)

    ssh_username = string
    ssh_keys     = list(string)

    os_type = optional(string, "")
    tags    = optional(list(string))
  }))
  default = {}
}
