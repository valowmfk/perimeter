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

module "vyos_vm" {
  source = "../modules/vyos_vm"

  for_each = var.vyos_configs

  vm_id        = each.value.vm_id
  name         = each.value.name
  node_name    = each.value.node
  template_id  = each.value.template_id
  datastore_id = each.value.datastore_id
  disk_size    = each.value.disk_size

  cores    = each.value.cores
  memory   = each.value.memory

  bridges = each.value.bridges

  ipv4_address = each.value.ipv4_address
  ipv4_gateway = each.value.ipv4_gateway
  dns_servers  = each.value.dns_servers
  ssh_username = each.value.ssh_username
  ssh_keys     = each.value.ssh_keys

  os_type = try(each.value.os_type, "")
  tags    = try(each.value.tags, [])

}
