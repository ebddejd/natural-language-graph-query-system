#!/usr/bin/env bash
# Run against a disposable kind cluster. Verify navigation state across pod deletion.
set -eu
kubectl create secret generic journey-provider --from-literal=LLM_MODEL='' --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f ops/k8s/local.yaml
kubectl rollout status deployment/journey-redis --timeout=120s
# Offline CI never sends model traffic. External readiness should remain unavailable.
for i in {1..90}; do
  pods=$(kubectl get pods -l app=journey --field-selector=status.phase=Running -o name)
  if [ "$(echo "$pods" | grep -c '^pod/' || true)" = 2 ]; then
    a=$(echo "$pods" | head -1); b=$(echo "$pods" | tail -1)
    if kubectl exec "$a" -- python -c "import urllib.request; urllib.request.urlopen('http://localhost:10000/healthz')" 2>/dev/null; then break; fi
  fi
  sleep 2
done
cookie=$(kubectl exec "$a" -- python -c "import urllib.request,json; u='http://localhost:10000'; q=urllib.request.Request(u+'/api/navigation',data=json.dumps(dict(action='point',field='destination',lat=43.49,lon=-80.52,revision=0)).encode(),headers={'Content-Type':'application/json','Origin':u}); r=urllib.request.urlopen(q); assert json.load(r)['revision']==1;print(r.headers['Set-Cookie'].split(';')[0])")
kubectl delete "$a" --wait=true
kubectl exec "$b" -- python -c "import urllib.request,json,sys; u='http://localhost:10000'; q=urllib.request.Request(u+'/api/navigation',data=json.dumps(dict(action='mode',mode='transit',revision=1)).encode(),headers={'Content-Type':'application/json','Origin':u,'Cookie':sys.argv[1]}); r=json.load(urllib.request.urlopen(q)); assert r['revision']==2 and r['destination']['lat']==43.49" "$cookie"
