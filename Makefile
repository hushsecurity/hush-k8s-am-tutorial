AWS_REGION=us-east-1
REGISTRY=public.ecr.aws/z0t8l6o4
DOCKER_NAME=hush-k8s-am-tutorial
RELEASE=$(shell git describe --always --first-parent --dirty --abbrev=10)
HELM_REPO=oci://ghcr.io/hushsecurity

.PHONY: all
all: build

.PHONY: clean
clean:
	@rm -rf build/

.PHONY: build
build:
	@docker build -t $(DOCKER_NAME):$(RELEASE) .
	@docker tag $(DOCKER_NAME):$(RELEASE) $(REGISTRY)/$(DOCKER_NAME):$(RELEASE)
	@docker tag $(DOCKER_NAME):$(RELEASE) $(DOCKER_NAME):latest

.PHONY: login
login:
	@aws ecr-public get-login-password --region $(AWS_REGION) | \
		docker login --username AWS --password-stdin public.ecr.aws

.PHONY: submit
submit: login
	@docker push $(REGISTRY)/$(DOCKER_NAME):$(RELEASE)

.PHONY: publish
publish:
	@mkdir build
	@helm package charts/hush-am-tutorial -d build/
	@helm push build/hush-am-tutorial-*.tgz $(HELM_REPO)

