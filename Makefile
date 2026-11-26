.PHONY: cluster deploy up down load

cluster:   ## Create the kind cluster with Terraform
	terraform -chdir=deploy/terraform init -input=false
	terraform -chdir=deploy/terraform apply -auto-approve -input=false

deploy:    ## Build, load and deploy the API with Ansible, then smoke-test it
	cd deploy/ansible && KUBECONFIG=$$(terraform -chdir=../terraform output -raw kubeconfig_path) ansible-playbook site.yml

up: cluster deploy

down:      ## Delete the cluster
	terraform -chdir=deploy/terraform destroy -auto-approve -input=false

load:      ## Send mixed traffic to the cluster
	python scripts/load.py --base http://localhost:8080 --seconds 60
