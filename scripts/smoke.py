"""Wait for an exact release, then exercise a real, low-cost navigation turn."""
import argparse
import http.cookiejar
import json
import time
import urllib.request

p=argparse.ArgumentParser()
p.add_argument('url')
p.add_argument('--revision',required=True)
a=p.parse_args()
base=a.url.rstrip('/')
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(path,body=None):
    req=urllib.request.Request(base+path, data=json.dumps(body).encode() if body else None,
                               headers={'Origin':base,'Content-Type':'application/json'})
    with opener.open(req,timeout=55) as response:
        return json.load(response)

for attempt in range(60):
    try:
        if call('/healthz')['revision']==a.revision and all(call('/readyz').values()):
            break
    except (OSError,ValueError,KeyError):pass
    time.sleep(10)
else:
    raise SystemExit('Expected ready revision did not become available.')
state=call('/api/navigation')
state=call('/api/navigation',dict(action='message', revision=state['revision'],
                                  text='Walk from Waterloo Public Library Main to University of Waterloo.'))
if 'walk' not in (state.get('agent_plan') or {}).get('modes',[]) or not (state.get('pending') or {}).get('options'):
    raise SystemExit('Real model and geocoding smoke check failed.')
print('Verified release, model extraction and live place matches.')
