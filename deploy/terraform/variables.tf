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

variable "api_node_port" {
  description = "NodePort the API Service listens on inside the cluster"
  type        = number
  default     = 30080
}

variable "api_host_port" {
  description = "Port on the host that forwards to the API NodePort"
  type        = number
  default     = 8080
}
