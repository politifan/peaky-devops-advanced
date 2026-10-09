terraform {
  required_version = "= 1.13.3"
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "= 4.6.0"
    }
  }
}
provider "docker" { host = "unix:///var/run/docker.sock" }
