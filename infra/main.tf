variable "public_key_file" {
  type = string
  validation {
    condition     = fileexists(var.public_key_file) && can(regex("^ssh-ed25519 ", trimspace(file(var.public_key_file))))
    error_message = "Provide an existing public SSH key file, never the private key."
  }
}
resource "docker_image" "node" {
  name         = "dva-node:24.04"
  keep_locally = true
  build { context = abspath("${path.module}/node-image") }
  triggers = {
    dockerfile = filesha256("${path.module}/node-image/Dockerfile")
    entrypoint = filesha256("${path.module}/node-image/entrypoint.sh")
  }
}
resource "docker_network" "lab" {
  name = "dva-tf-net"
  labels {
    label = "course"
    value = "dva"
  }
}
module "node" {
  source = "./modules/node"
  for_each = {
    a = { ssh_port = 22220, http_port = 22280 }
    b = { ssh_port = 22221, http_port = 22281 }
  }
  name            = "dva-node-${each.key}"
  image_id        = docker_image.node.image_id
  network_name    = docker_network.lab.name
  public_key_file = abspath(var.public_key_file)
  ssh_port        = each.value.ssh_port
  http_port       = each.value.http_port
}
output "nodes" {
  value = { for key, node in module.node : key => node.connection }
}
