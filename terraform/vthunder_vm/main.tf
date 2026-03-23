terraform {
  required_version = ">= 1.5.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.98.0"
    }
  }
}

provider "proxmox" {
  endpoint = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure = true
}

module "vthunder_vm" {
  source = "../modules/vthunder_vm"

  for_each = var.vthunder_configs

  vm_id        = each.value.vm_id
  name         = each.value.name
  node_name    = each.value.node
  template_id  = each.value.template_id
  datastore_id = each.value.datastore_id

  cores     = each.value.cores
  memory    = each.value.memory
  disk_size = lookup(each.value, "disk_size", 40)  # Default to 40GB if not specified
  bridges   = each.value.bridges
}

# Moved to outputs.tf
#
# output "vthunder_mgmt_mac" {
#   value = {
#     for name, cfg in var.vthunder_configs :
#     name => module.vthunder_vm[name].mgmt_mac
#   }
# }
