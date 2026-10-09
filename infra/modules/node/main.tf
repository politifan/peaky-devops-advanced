terraform {
  required_providers {
    docker = { source = "kreuzwerker/docker", version = "= 4.6.0" }
  }
}
variable "name" { type = string }
variable "image_id" { type = string }
variable "network_name" { type = string }
variable "public_key_file" { type = string }
variable "ssh_port" { type = number }
variable "http_port" { type = number }
resource "docker_volume" "data" {
  name = "${var.name}-data"
  labels {
    label = "course"
    value = "dva"
  }
  lifecycle { prevent_destroy = true }
}
resource "docker_container" "node" {
  name     = var.name
  hostname = var.name
  image    = var.image_id
  must_run = true
  restart  = "no"
  networks_advanced { name = var.network_name }
  ports {
    internal = 22
    external = var.ssh_port
    ip = "127.0.0.1"
  }
  ports {
    internal = 8080
    external = var.http_port
    ip = "127.0.0.1"
  }
  mounts {
    type      = "bind"
    source    = var.public_key_file
    target    = "/run/training/authorized_key"
    read_only = true
  }
  volumes {
    volume_name = docker_volume.data.name
    container_path = "/srv/dva"
  }
  labels {
    label = "course"
    value = "dva"
  }
}
output "connection" {
  value = { name = var.name, host = "127.0.0.1", user = "lab", ssh_port = var.ssh_port, http_port = var.http_port }
}
