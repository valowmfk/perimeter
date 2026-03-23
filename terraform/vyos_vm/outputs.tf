output "vm_ids" {
  description = "Map of VyOS VM name to VMID"
  value = {
    for name, vm in module.vyos_vm :
    name => vm.vm_id
  }
}

output "vm_names" {
  description = "Map of VyOS VM name to hostname"
  value = {
    for name, vm in module.vyos_vm :
    name => vm.name
  }
}

output "vm_nodes" {
  description = "Map of VyOS VM name to Proxmox node"
  value = {
    for name, vm in module.vyos_vm :
    name => vm.node_name
  }
}

output "vm_ips" {
  description = "Map of VyOS VM name to IPv4 addresses (requires QEMU guest agent)"
  value = {
    for name, vm in module.vyos_vm :
    name => vm.ipv4_addresses
  }
}
