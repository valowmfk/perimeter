###############################################
# vThunder module — BPG / Proxmox
#
# Notes:
# - Supports multiple NICs via the bridges list variable
# - First NIC is typically management, additional NICs are data plane
# - IP / gateway / DNS are currently *not* applied via
#   Proxmox cloud-init; they live in vthunder_configs
#   purely for our scripts / UI and future use.
###############################################

terraform {
  required_providers {
    proxmox = {
      source = "bpg/proxmox"
    }
  }
}

###############################################
# vThunder VM Resource
###############################################
resource "proxmox_virtual_environment_vm" "vthunder" {
  vm_id     = var.vm_id
  node_name = var.node_name
  name      = var.name

  # Start the VM immediately and have it come up on host boot
  started        = true
  on_boot        = var.on_boot
  stop_on_destroy = var.stop_on_destroy

  # ACOS runs fine with the generic Linux type here
  operating_system {
    type = "l26"
  }

  cpu {
    cores = var.cores
    type  = var.cpu_type
  }

  memory {
    dedicated = var.memory
  }

  # Clone from an existing vThunder template
  clone {
    node_name = var.node_name
    vm_id     = var.template_id
  }

  # Single system disk on SCSI
  disk {
    datastore_id = var.datastore_id
    interface    = "scsi0"
    size         = var.disk_size
    discard      = "on"
  }

  # We normally just use serial console for appliances
  vga {
    type = "serial0"
  }

  # Dynamic NICs - first is management, rest are data plane
  dynamic "network_device" {
    for_each = var.bridges
    content {
      bridge = network_device.value
    }
  }

  lifecycle {
    ignore_changes = [
      clone,
      boot_order,
      keyboard_layout,
      disk,
      cpu,
      memory,
      serial_device,
      scsi_hardware,
      tags,
      network_device,
    ]
  }
}
