###############################################
# Accept BPG Proxmox provider from root module
###############################################
terraform {
  required_providers {
    proxmox = {
      source = "bpg/proxmox"
    }
  }
}

###############################################
# Linux Cloud-Init VM (BPG provider)
###############################################

resource "proxmox_virtual_environment_vm" "linux" {
  vm_id     = var.vm_id
  node_name = var.node_name
  name      = var.name

  started   = true
  on_boot   = true

  # Match the template hardware exactly
  machine       = "q35"
  bios          = "ovmf"
  scsi_hardware = "virtio-scsi-single"
  boot_order    = ["scsi0"]

  description = var.description

  tags = concat(var.tags, var.os_type == "" ? [] : [var.os_type])

  agent {
    enabled = true
    timeout = "0s"
  }

  clone {
    node_name = var.node_name
    vm_id     = var.template_id
  }

  operating_system {
    type = "l26"
  }

  cpu {
    cores = var.cores
    # Critical: match template CPU to avoid kernel panics
    type  = var.cpu_type
  }

  memory {
    dedicated = var.memory
  }

  disk {
    datastore_id = var.datastore_id
    interface    = "scsi0"
    size         = var.disk_size
    discard      = "on"
    iothread     = true
  }

  vga {
    type = "serial0"
  }

  ###############################################
  # Networking - Dynamic NICs
  # First NIC gets cloud-init IP config, additional NICs need manual config
  ###############################################
  dynamic "network_device" {
    for_each = var.bridges
    content {
      bridge = network_device.value
    }
  }

  ###############################################
  # Cloud-init
  ###############################################
  initialization {
    datastore_id = var.datastore_id

    ip_config {
      ipv4 {
        address = var.ipv4_address
        gateway = var.ipv4_gateway
      }
    }

    dns {
      servers = var.dns_servers
    }

      user_account {
      username = var.ssh_username
      keys     = var.ssh_keys
    }
  }

  lifecycle {
    ignore_changes = [
      clone,
      efi_disk,
      initialization[0].interface,
      initialization[0].dns[0].domain,
      initialization[0].user_account[0].password,
      disk,
      cpu,
      memory,
      agent[0].timeout,
      keyboard_layout,
      serial_device,
      network_device,
      tags,
    ]
  }
}

