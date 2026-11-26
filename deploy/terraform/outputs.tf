output "cluster_name" {
  value = kind_cluster.this.name
}

output "kubeconfig_path" {
  value = kind_cluster.this.kubeconfig_path
}

output "urls" {
  value = { for name, m in var.port_mappings : name => "http://localhost:${m.host_port}" }
}
