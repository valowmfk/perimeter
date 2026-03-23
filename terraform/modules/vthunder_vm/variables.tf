variable "vm_id" {
  description = "Proxmox VM ID (unique across the cluster)"
  type        = number
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
  description = "VMID of the ACOS template to clone (e.g. 9005, 9006, 9007)"
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

variable "cpu_type" {
  description = "CPU type to use; 'host' for best performance"
  type        = string
  default     = "host"
}

variable "disk_size" {
  description = "Root disk size in GB"
  type        = number
  default     = 40
}

variable "bridges" {
  description = "List of network bridges for NICs (first is management, rest are data plane)"
  type        = list(string)
  default     = ["vmbr0"]
}

variable "on_boot" {
  description = "Start VM automatically when Proxmox host boots"
  type        = bool
  default     = true
}

variable "stop_on_destroy" {
  description = "Stop the VM before destroying it (prevents force-kill)"
  type        = bool
  default     = true
}
