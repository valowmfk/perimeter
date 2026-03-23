output "vthunder_vm_ids" {
  description = "Map of vThunder name to VMID"
  value = {
    for name, cfg in var.vthunder_configs :
    name => module.vthunder_vm[name].vm_id
  }
}

output "vthunder_names" {
  description = "Map of vThunder name to hostname"
  value = {
    for name, cfg in var.vthunder_configs :
    name => module.vthunder_vm[name].name
  }
}

output "vthunder_nodes" {
  description = "Map of vThunder name to Proxmox node"
  value = {
    for name, cfg in var.vthunder_configs :
    name => module.vthunder_vm[name].node_name
  }
}

output "vthunder_mgmt_mac" {
  description = "Map of vThunder name to management MAC address"
  value = {
    for name, cfg in var.vthunder_configs :
    name => module.vthunder_vm[name].mgmt_mac
  }
}
