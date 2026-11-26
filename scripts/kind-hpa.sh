#!/usr/bin/env bash
# Disposable kind only. Isolate CPU scaling from unavailable upstream credentials.
set -eu
test "$(kubectl config current-context)" = kind-journey-ci
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.0/components.yaml
kubectl patch deployment -n kube-system metrics-server --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
kubectl rollout status -n kube-system deploy/metrics-server --timeout=180s
kubectl patch deployment journey --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/healthz"}]'
kubectl rollout status deployment/journey --timeout=180s
for pod in $(kubectl get pods -l app=journey -o name); do
  kubectl exec "$pod" -- python -c 'import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;t=time.monotonic()+240\nwhile time.monotonic()<t: pass"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)'
done
for i in {1..48}; do
  ready=$(kubectl get deployment journey -o jsonpath='{.status.readyReplicas}')
  kubectl get hpa journey
  if [ "${ready:-0}" -ge 3 ]; then exit 0; fi
  sleep 5
done
exit 1
