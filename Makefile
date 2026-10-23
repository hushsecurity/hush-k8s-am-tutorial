AWS_REGION=us-east-1
REGISTRY=public.ecr.aws/z0t8l6o4
DOCKER_NAME=hush-k8s-am-tutorial
RELEASE=$(shell git describe --always --first-parent --dirty --abbrev=10)
HELM_REPO=oci://ghcr.io/hushsecurity
CHART_NAME=hush-am-tutorial
CHART_DIR=charts/$(CHART_NAME)
# A release is one version for both: the chart's version, and the image its
# appVersion points at.
VERSION:=$(shell sed -n 's/^version: *"*\([^"]*\)"*/\1/p' $(CHART_DIR)/Chart.yaml)
APP_VERSION:=$(shell sed -n 's/^appVersion: *"*\([^"]*\)"*/\1/p' $(CHART_DIR)/Chart.yaml)
# The platforms the pushed images are built for.
PLATFORMS?=linux/amd64,linux/arm64

# Prints those of PLATFORMS the current buildx builder can build together,
# comma-separated. The docker driver builds one platform at a time, so with it
# that is only the machine's own. Fails if the builder cannot be inspected.
define buildable_platforms
info=$$(docker buildx inspect --bootstrap) || exit 1; \
if echo "$$info" | grep -q '^Driver: *docker$$'; then \
	can=$$(docker version -f '{{.Server.Os}}/{{.Server.Arch}}'); \
else \
	can=$$(echo "$$info" | sed -n 's/^Platforms: *//p' | tr -d ' *' | tr , '\n'); \
fi; \
for p in $$(echo $(PLATFORMS) | tr , ' '); do \
	echo "$$can" | grep -qx "$$p" && printf '%s\n' "$$p"; \
done | paste -sd, -
endef

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

# An image of this commit, for trying it out before a release: for the
# PLATFORMS this machine can build, or just its own.
.PHONY: submit
submit: login
	@platforms=$$($(buildable_platforms)) || exit 1; \
	docker buildx build $${platforms:+--platform $$platforms} --push \
		-t $(REGISTRY)/$(DOCKER_NAME):$(RELEASE) .

# A release is built for every one of PLATFORMS, never fewer.
.PHONY: check-platforms
check-platforms:
	@platforms=$$($(buildable_platforms)) || exit 1; \
	if [ "$$platforms" != "$(PLATFORMS)" ]; then \
		echo "This buildx builder cannot build all of $(PLATFORMS) (only: $${platforms:-none})." >&2; \
		echo "Release from CI, or set up emulation and a builder for them first." >&2; \
		exit 1; \
	fi

.PHONY: check-version
check-version:
	@if [ "$(VERSION)" != "$(APP_VERSION)" ]; then \
		echo "$(CHART_DIR)/Chart.yaml: version $(VERSION) and appVersion $(APP_VERSION) must match" >&2; \
		exit 1; \
	fi

# A release must be exactly a commit: no changed or untracked files around it.
.PHONY: check-clean
check-clean:
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "Commit or remove these before releasing:" >&2; \
		git status --short >&2; \
		exit 1; \
	fi

# Prints whether this version is on ghcr: "released" or "unreleased", and fails
# when that cannot be told. Only ghcr's "not found" for this exact version
# proves it is free. Without read access ghcr answers "denied" whether or not
# the version exists, so that fails too; the one exception is the very first
# release, when there is no chart yet: run it with FIRST_RELEASE=1.
.PHONY: release-state
release-state:
	@command -v helm >/dev/null || { echo "helm is not installed" >&2; exit 1; }
	@error=$$(helm show chart $(HELM_REPO)/$(CHART_NAME) --version $(VERSION) 2>&1 >/dev/null) && { \
		echo released; \
		exit 0; \
	}; \
	case "$$error" in \
	*"$(CHART_NAME):$(VERSION): not found"*) echo unreleased;; \
	*"denied: requested access to the resource is denied"*) \
		if [ "$(FIRST_RELEASE)" != 1 ]; then \
			echo "ghcr denied the lookup of $(CHART_NAME) $(VERSION), so it may already be released." >&2; \
			echo "Log in first (helm registry login ghcr.io), or set FIRST_RELEASE=1 for the very first release." >&2; \
			exit 1; \
		fi; \
		echo unreleased;; \
	*) echo "Could not check whether $(CHART_NAME) $(VERSION) is released:" >&2; echo "$$error" >&2; exit 1;; \
	esac

# A released version is never replaced.
.PHONY: check-unreleased
check-unreleased: check-version
	@state=$$($(MAKE) -s release-state) || exit 1; \
	if [ "$$state" != unreleased ]; then \
		echo "$(CHART_NAME) $(VERSION) is already released; bump the version in $(CHART_DIR)/Chart.yaml" >&2; \
		exit 1; \
	fi

# Release this version: build the image and the chart from the committed files
# only, push the image as :<version>, then the chart. The chart goes last, so a
# release that failed halfway can be run again: the rerun replaces only an image
# no released chart points at yet. (Tag immutability in the image repository
# would block that.)
.PHONY: publish
publish: check-clean check-unreleased check-platforms clean login
	@git archive --format=tar HEAD | docker buildx build --platform $(PLATFORMS) --push \
		-t $(REGISTRY)/$(DOCKER_NAME):$(VERSION) -
	@mkdir -p build/src
	@git archive --format=tar HEAD $(CHART_DIR) | tar -x -C build/src
	@helm package build/src/$(CHART_DIR) -d build/
	@helm push build/$(CHART_NAME)-$(VERSION).tgz $(HELM_REPO)
