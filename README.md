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

## Serving the page at its own address

To reach the page without kubectl port-forward, such as behind a load
balancer or ingress, expose only the `hush-am-tutorial-dashboard` Service
(port 8080), and set:

- `dashboard.allowedHosts`: the host names the page is served under. The
  page's buttons are refused under any other name.
- `dashboard.basicAuth.existingSecret`: a Secret in the tutorial's namespace,
  with keys `username` and `password`, that the page then asks for. Serve the
  page over HTTPS, so the password is not sent in the clear.

## Leaving it running

For an install left running, set `postgres.persistence.enabled` at install:
otherwise the database starts over empty if its pod moves to another node, and
the apps lose their logins.

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
