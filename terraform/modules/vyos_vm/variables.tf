variable "os_type" {
  description = "Operating system label (vyos)"
  type        = string
  default     = ""
}

variable "cpu_type" {
  description = "CPU type to use; 'host' for best performance (requires matching CPUs for live migration)"
  type        = string
  default     = "host"
}

variable "vm_id" {
  description = "Proxmox VM ID (unique across the cluster)"
  type        = number
}

variable "description" {
  description = "Description shown in Proxmox GUI Notes field"
  type        = string
  default     = ""
}

variable "name" {
  description = "VM hostname"
  type        = string
}

variable "node_name" {
  description = "Proxmox node to create the VM on"
  type        = string
}

variable "template_id" {
  description = "VMID of the VyOS template to clone"
  type        = number
}

variable "datastore_id" {
  description = "Proxmox storage pool for VM disks (e.g. zfs-pool)"
  type        = string
}

variable "memory" {
  description = "RAM in MB"
  type        = number
}

variable "cores" {
  description = "Number of vCPU cores"
  type        = number
}

variable "disk_size" {
  description = "Root disk size in GB"
  type        = number
}

variable "bridges" {
  description = "List of network bridges for NICs (first NIC gets cloud-init IP config)"
  type        = list(string)
  default     = ["vmbr0"]
}

variable "ipv4_address" {
  description = "Static IPv4 address in CIDR notation (e.g. 10.1.55.60/24)"
  type        = string
}

variable "ipv4_gateway" {
  description = "IPv4 default gateway"
  type        = string
}

variable "dns_servers" {
  description = "List of DNS server IPs for cloud-init"
  type        = list(string)
}

variable "ssh_username" {
  description = "Default SSH user created by cloud-init"
  type        = string
}

variable "ssh_keys" {
  description = "SSH public keys to inject via cloud-init"
  type        = list(string)
  sensitive   = true
}

variable "tags" {
  description = "Proxmox tags to apply to the VM"
  type        = list(string)
  default     = []
}
