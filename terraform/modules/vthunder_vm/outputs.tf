output "vm_id" {
  description = "VMID of the created vThunder VM"
  value       = proxmox_virtual_environment_vm.vthunder.vm_id
}

output "name" {
  description = "Name of the vThunder VM"
  value       = proxmox_virtual_environment_vm.vthunder.name
}

output "node_name" {
  description = "Proxmox node where the vThunder VM runs"
  value       = proxmox_virtual_environment_vm.vthunder.node_name
}

output "mgmt_mac" {
  description = "Management MAC address of the vThunder VM"
  value       = proxmox_virtual_environment_vm.vthunder.network_device[0].mac_address
}
