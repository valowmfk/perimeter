output "vm_id" {
  description = "VMID of the created VM"
  value       = proxmox_virtual_environment_vm.vyos.vm_id
}

output "name" {
  description = "Name of the VM"
  value       = proxmox_virtual_environment_vm.vyos.name
}

output "node_name" {
  description = "Proxmox node where the VM runs"
  value       = proxmox_virtual_environment_vm.vyos.node_name
}

output "ipv4_addresses" {
  description = "IPv4 addresses reported by Proxmox (when QEMU guest agent works)"
  value       = proxmox_virtual_environment_vm.vyos.ipv4_addresses
}
