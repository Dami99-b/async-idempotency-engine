variable "cluster_name" {
  description = "Name of the kind cluster"
  type        = string
  default     = "idempotency"
}

variable "worker_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 2
}

variable "port_mappings" {
  description = "NodePorts exposed on localhost, keyed by service"
  type = map(object({
    node_port = number
    host_port = number
  }))
  default = {
    api        = { node_port = 30080, host_port = 8080 }
    grafana    = { node_port = 30300, host_port = 3000 }
    prometheus = { node_port = 30090, host_port = 9090 }
  }
}
