{{- define "hush-am-tutorial.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "hush-am-tutorial.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "hush-am-tutorial.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "hush-am-tutorial.postgresName" -}}
{{- printf "%s-postgres" (include "hush-am-tutorial.fullname" .) -}}
{{- end -}}

{{- define "hush-am-tutorial.postgresHost" -}}
{{- printf "%s.%s.svc.cluster.local" (include "hush-am-tutorial.postgresName" .) .Release.Namespace -}}
{{- end -}}

{{- /* Deployment and AccessPolicy name of an app; takes (list $ appName). */ -}}
{{- define "hush-am-tutorial.appName" -}}
{{- printf "%s-%s" (include "hush-am-tutorial.fullname" (index . 0)) (index . 1) -}}
{{- end -}}

{{- /* Space-separated names of the apps the chart grants a policy. */ -}}
{{- define "hush-am-tutorial.policyApps" -}}
{{- $names := list -}}
{{- range $app, $cfg := .Values.apps -}}
{{- if $cfg.privilege -}}
{{- $names = append $names $app -}}
{{- end -}}
{{- end -}}
{{- join " " $names -}}
{{- end -}}

{{- define "hush-am-tutorial.appServiceAccountName" -}}
{{- printf "%s-sa" (include "hush-am-tutorial.appName" .) -}}
{{- end -}}

{{- define "hush-am-tutorial.dashboardName" -}}
{{- printf "%s-dashboard" (include "hush-am-tutorial.fullname" .) -}}
{{- end -}}

{{- /* Where the apps send their reports. */ -}}
{{- define "hush-am-tutorial.dashboardUrl" -}}
{{- printf "http://%s-reports.%s.svc.cluster.local:%v" (include "hush-am-tutorial.dashboardName" .) .Release.Namespace .Values.dashboard.reportPort -}}
{{- end -}}

{{- /* The names the tutorial page shows in its commands and UI values, as JSON. */ -}}
{{- define "hush-am-tutorial.tutorialConfig" -}}
{{- $apps := list -}}
{{- range $app := keys .Values.apps | sortAlpha -}}
{{- $cfg := index $.Values.apps $app -}}
{{- $apps = append $apps (dict
  "name" $app
  "deployment" (include "hush-am-tutorial.appName" (list $ $app))
  "serviceAccount" (include "hush-am-tutorial.appServiceAccountName" (list $ $app))
  "replicas" (hasKey $cfg "replicas" | ternary $cfg.replicas 1)
  "policy" (ternary (include "hush-am-tutorial.appName" (list $ $app)) "" (not (empty $cfg.privilege)))
  "privilege" (ternary (printf "%s-%s" (include "hush-am-tutorial.fullname" $) ($cfg.privilege | default "")) "" (not (empty $cfg.privilege)))) -}}
{{- end -}}
{{- toJson (dict
  "namespace" .Release.Namespace
  "release" .Release.Name
  "hushNamespace" .Values.hush.namespace
  "credential" .Values.hush.credentialName
  "privileges" (list (printf "%s-readwrite-all" (include "hush-am-tutorial.fullname" .)) (printf "%s-read-all" (include "hush-am-tutorial.fullname" .)))
  "dashboard" (dict "service" (include "hush-am-tutorial.dashboardName" .) "port" .Values.dashboard.port)
  "apps" $apps) -}}
{{- end -}}

{{- define "hush-am-tutorial.hooksServiceAccountName" -}}
{{- printf "%s-hooks" (include "hush-am-tutorial.fullname" .) -}}
{{- end -}}

{{- define "hush-am-tutorial.clientImage" -}}
{{- printf "%s:%s" .Values.client.image.repository (.Values.client.image.tag | default .Chart.AppVersion) -}}
{{- end -}}

{{- /*
The Postgres root password: postgres.rootPassword if set, else the one the
release already uses (read back from its Secret), else a new random one.
Stored in .Values so that every template of this render gets the same one.
*/ -}}
{{- define "hush-am-tutorial.postgresRootPassword" -}}
{{- if not .Values.postgres.rootPassword -}}
{{- $secret := lookup "v1" "Secret" .Values.hush.namespace (printf "%s-postgres-root-secret" (include "hush-am-tutorial.fullname" .)) -}}
{{- $password := "" -}}
{{- if $secret -}}
{{- $password = index $secret.data "password" | b64dec -}}
{{- else -}}
{{- $password = randAlphaNum 24 -}}
{{- end -}}
{{- $_ := set .Values.postgres "rootPassword" $password -}}
{{- end -}}
{{- .Values.postgres.rootPassword -}}
{{- end -}}

{{- /*
Security settings that meet the restricted Pod Security Standard. The pod's
takes the user id to run as; the container's is the same for every container.
*/ -}}
{{- define "hush-am-tutorial.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "hush-am-tutorial.containerSecurityContext" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end -}}
