.PHONY: cluster deploy up chaos down load test

KUBECONFIG_PATH = $$(terraform -chdir=deploy/terraform output -raw kubeconfig_path)

test:      ## Unit tests
	pytest -q

cluster:   ## Create the kind cluster with Terraform
	terraform -chdir=deploy/terraform init -input=false
	terraform -chdir=deploy/terraform apply -auto-approve -input=false

deploy:    ## Build and deploy the API, Redis, Prometheus and Grafana with Ansible, then smoke-test
	cd deploy/ansible && KUBECONFIG=$$(terraform -chdir=../terraform output -raw kubeconfig_path) ansible-playbook site.yml

up: cluster deploy   ## Everything: cluster + deploy

chaos:     ## Pod kill, rolling restart and Redis outage, each under load
	KUBECONFIG=$(KUBECONFIG_PATH) scripts/chaos.sh all

load:      ## Send mixed traffic to the cluster
	python scripts/load.py --base http://localhost:8080 --seconds 60

down:      ## Delete the cluster
	terraform -chdir=deploy/terraform destroy -auto-approve -input=false
