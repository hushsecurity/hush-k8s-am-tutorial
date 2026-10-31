# Hush Unified Access Management tutorial

See Hush Unified Access Management work in your own cluster in a few guided
steps. The chart deploys two sample apps and a Postgres database in their own
namespace; nothing of yours is touched.

## Before you start

- A Kubernetes cluster with Hush Access Management installed
- Helm 3 and kubectl
- Access to the Hush UI

## Run it

```bash
helm install tutorial oci://ghcr.io/hushsecurity/hush-am-tutorial \
  --namespace husham-tutorial --create-namespace
kubectl port-forward -n husham-tutorial svc/hush-am-tutorial-dashboard 8080:8080
```

Open <http://localhost:8080> and follow the page. If the page loses the
dashboard, run the port-forward again.

## What you'll see

1. **Credentials, delivered**: each app signs in with its own login, created
   by Hush when its pod started.
2. **Access per identity**: same image, different policy: billing reads and
   writes, analytics' writes are denied.
3. **Rotation without downtime**: rotate in the Hush UI; old and new
   revisions keep working side by side.
4. **Onboard a new app** (optional): create a policy for a new app, then
   deploy it with exactly that access.
5. **Recap**, and how to clean up.

**Start over**, at the top of the page, restarts the apps as they were
installed and returns to step 1.

## What it installs

- In `husham-tutorial`: the billing and analytics apps, the refunds app (with
  no pods until you deploy it in step 4), Postgres, and the tutorial page.
- In Hush's namespace: a connector to that Postgres, two privileges, and one
  policy per app.

## Clean up

If you created the refunds policy in the Hush UI, delete it there first.

```bash
helm uninstall tutorial -n husham-tutorial
kubectl delete namespace husham-tutorial
```
