#!/usr/bin/env bash
# Run against a disposable kind cluster. Verify navigation state across pod deletion.
set -eu
kubectl create secret generic journey-provider --from-literal=LLM_MODEL='' --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f ops/k8s/local.yaml
kubectl rollout status deployment/journey-redis --timeout=120s
# Offline CI does not use external providers. Wait for each app to reach Redis
# through the cluster Service before sending any session writes.
pod_ready() {
  kubectl exec "$1" -- python -c "
import os
import urllib.request
from redis import Redis

with urllib.request.urlopen('http://localhost:10000/healthz', timeout=3):
    pass
with Redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=3, socket_timeout=3) as client:
    assert client.ping()
"
}

ready=false
for i in {1..90}; do
  pods=$(kubectl get pods -l app=journey --field-selector=status.phase=Running -o name)
  if [ "$(echo "$pods" | grep -c '^pod/' || true)" = 2 ]; then
    a=$(echo "$pods" | head -1); b=$(echo "$pods" | tail -1)
    if pod_ready "$a" 2>/dev/null && pod_ready "$b" 2>/dev/null; then
      ready=true
      break
    fi
  fi
  sleep 2
done
if [ "$ready" != true ]; then
  kubectl get pods,services,endpointslices
  for pod in $pods; do
    pod_ready "$pod" || true
  done
  exit 1
fi
cookie=$(kubectl exec "$a" -- python -c "import urllib.request,json; u='http://localhost:10000'; q=urllib.request.Request(u+'/api/navigation',data=json.dumps(dict(action='point',field='destination',lat=43.49,lon=-80.52,revision=0)).encode(),headers={'Content-Type':'application/json','Origin':u}); r=urllib.request.urlopen(q); assert json.load(r)['revision']==1;print(r.headers['Set-Cookie'].split(';')[0])")
kubectl delete "$a" --wait=true
kubectl exec "$b" -- python -c "import urllib.request,json,sys; u='http://localhost:10000'; q=urllib.request.Request(u+'/api/navigation',data=json.dumps(dict(action='mode',mode='transit',revision=1)).encode(),headers={'Content-Type':'application/json','Origin':u,'Cookie':sys.argv[1]}); r=json.load(urllib.request.urlopen(q)); assert r['revision']==2 and r['destination']['lat']==43.49" "$cookie"
