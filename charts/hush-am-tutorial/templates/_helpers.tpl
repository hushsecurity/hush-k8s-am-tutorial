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

{{- define "hush-am-tutorial.serviceAccountName" -}}
{{- printf "%s-sa" (include "hush-am-tutorial.fullname" .) -}}
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
