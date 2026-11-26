"""Single-instance hosted entry point: bounded, isolated visitor sessions."""
import json, os, secrets, threading, time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from app import Service
from core import ROOT, Error, Engine
from generation import generate
from runtime import Admission
import model_config
import telemetry
from prometheus_client import generate_latest
from walking import GeoProvider, WalkSession
from navigation import NavigationSession

class HostedApp:
    def __init__(self, origin=None):
        self.origin=(origin or os.environ.get('RENDER_EXTERNAL_URL') or os.environ.get('PUBLIC_URL','')).rstrip('/')
        if not self.origin:raise RuntimeError('Set PUBLIC_URL or RENDER_EXTERNAL_URL to the public HTTPS URL')
        from shared_journey import SharedJourney
        self.shared=SharedJourney(os.environ['REDIS_URL']) if os.getenv('REDIS_URL') else None
        self.probe_lock=threading.Lock();self.probe_until=0
        self.geo=GeoProvider();self.walk_admission=Admission(2,int(os.getenv('NAV_QUEUE_CAPACITY','12')),2)
        self.sessions={};self.lock=threading.Lock();self.engine=Engine();self.admission=Admission(4,16,2)
        self.owner_settings={'base_url':'https://api.openai.com/v1','model':os.getenv('LLM_MODEL','').strip(),'api_key':os.getenv('LLM_API_KEY','').strip()}
        self.model_window=time.monotonic();self.model_calls=0
        self.model_limit=int(os.getenv('LLM_REQUESTS_PER_MINUTE','20'))
        if self.model_limit<1:raise ValueError('LLM_REQUESTS_PER_MINUTE must be positive')
        self.initial=json.loads((ROOT/'data/initial-graph.json').read_text())
    def readiness(self):
        # Probe at most once a minute per process; keep expensive generation out of probes.
        import urllib.request
        now=time.monotonic()
        with self.probe_lock:
            if now < self.probe_until:return self.probe_result
            result={'model':False,'maps':False}
            if self.owner_settings['api_key'] and self.owner_settings['model']:
                from urllib.parse import quote
                url=self.owner_settings['base_url']+'/models/'+quote(self.owner_settings['model'],safe='')
                req=urllib.request.Request(url,headers={'Authorization':'Bearer '+self.owner_settings['api_key']})
                try:
                    with urllib.request.urlopen(req,timeout=3) as response:result['model']=response.status==200
                except OSError:pass
            if os.getenv('GEOAPIFY_API_KEY'):
                try:result['maps']=bool(self.geo.geocode('University of Waterloo'))
                except Error:pass
            if self.shared is not None:
                try:result['redis']=bool(self.shared.client.ping())
                except Exception:result['redis']=False
            self.probe_result=result;self.probe_until=now+60
            return result

    def session(self,cookie):
        jar=SimpleCookie()
        try:jar.load(cookie)
        except Exception:pass
        sid=jar['graph_session'].value if 'graph_session' in jar else ''
        now=time.monotonic()
        with self.lock:
            for key in list(self.sessions):
                if self.sessions[key]['expires']<now and not self.sessions[key]['active']:del self.sessions[key]
            if sid not in self.sessions:
                if len(self.sessions)>=128:raise Error('Visitor capacity reached; please try later',503)
                sid=self.shared.identify(sid) if self.shared else secrets.token_urlsafe(32);service=Service(self.initial)
                service.engine=self.engine;service.admission=self.admission
                self.sessions[sid]={'service':service,'walk':WalkSession(self.geo),'nav':NavigationSession(self.geo),'config':{},'active':0,'window':now,'count':0,'expires':now+1800}
            session=self.sessions[sid]
            if now-session['window']>=60:session.update(window=now,count=0)
            if session['count']>=90:raise Error('Too many requests; wait a minute',429)
            session['count']+=1;session['active']+=1;session['expires']=now+1800
            return sid,session
    def reserve_model_call(self):
        if self.shared:return self.shared.allow_model(self.model_limit)
        with self.lock:
            now=time.monotonic()
            if now-self.model_window>=60:self.model_window=now;self.model_calls=0
            if self.model_calls>=self.model_limit:raise Error('Too many AI requests. Please try again shortly.',429)
            self.model_calls+=1
    def __call__(self,env,start_response):
        sid=None;session=None;token=None;ctype='application/json';status=200
        started=time.monotonic();rid=secrets.token_hex(12);ctx=telemetry.request_id.set(rid)
        try:
            path=env.get('PATH_INFO','/');method=env.get('REQUEST_METHOD','GET')
            if method not in ('GET','POST'):raise Error('Method not allowed',405)
            if method=='GET' and path in ('/api/health','/healthz'):result={'status':'ok','revision':os.getenv('RENDER_GIT_COMMIT') or os.getenv('RELEASE_SHA')}
            elif method=='GET' and path=='/readyz':
                result=self.readiness()
                if not all(result.values()):status=503
            elif method=='GET' and path=='/metrics':
                ctype='text/plain';result=generate_latest().decode()
            else:
                expected=urlsplit(self.origin).netloc
                if env.get('HTTP_HOST')!=expected:raise Error('Unexpected host',403)
                if method=='POST' and env.get('HTTP_ORIGIN')!=self.origin:raise Error('Same-origin request required',403)
                sid,session=self.session(env.get('HTTP_COOKIE',''));service=session['service']
                token=model_config.visitor_settings.set({'settings':self.owner_settings})
                if method=='GET':
                    if path in ('/','/walk'):
                        ctype='text/html';result=(ROOT/'web/navigation.html').read_text()
                    elif path=='/api/navigation':result=self.shared.read(sid,self.geo) if self.shared else session['nav'].public()
                    elif path=='/api/walk':result=session['walk'].public('告诉我从哪里出发、去哪里，以及愿意额外走多久。')
                    elif path.startswith('/api/walk/tile/'):
                        parts=path.removeprefix('/api/walk/tile/').removesuffix('.png').split('/')
                        if len(parts)!=3:raise Error('Invalid tile')
                        result=self.geo.tile(*map(int,parts));ctype='image/png'
                    elif path=='/lab':
                        ctype='text/html';result=(ROOT/'web/index.html').read_text()
                        result=result.replace('密钥只保存在本机服务内存中。','模型由站点提供，无需访客填写密钥。')
                        result=result.replace('刷新本机指标','刷新会话指标')
                    elif path=='/api/model':result=dict(model_config.public(),shared=True)
                    elif path=='/api/graph':
                        v=parse_qs(env.get('QUERY_STRING','')).get('version',[None])[0]
                        result=service.store.get(int(v) if v is not None else None).public()
                    elif path=='/api/metrics':result=dict(service.metrics.snapshot(),admission=self.admission.state())
                    else:raise Error('Not found',404)
                else:
                    if env.get('CONTENT_TYPE','').split(';')[0]!='application/json':raise Error('Use application/json',415)
                    size=int(env.get('CONTENT_LENGTH') or 0)
                    if not 0<size<=200000:raise Error('Request too large or empty',413)
                    body=json.loads(env['wsgi.input'].read(size))
                    if not isinstance(body,dict):raise Error('Expected JSON object')
                    if path in ('/api/model','/api/model/clear','/api/model/test'):
                        raise Error('共享模型由站点管理员配置，访客不能修改',403)
                    elif path=='/api/navigation':
                        self.walk_admission.enter()
                        try:result=self.shared.update(sid,self.geo,body,self.reserve_model_call) if self.shared else session['nav'].handle(body,self.reserve_model_call)
                        finally:self.walk_admission.leave()
                    elif path=='/api/walk':
                        self.walk_admission.enter()
                        try:result=session['walk'].handle(body,self.reserve_model_call)
                        finally:self.walk_admission.leave()
                    elif path=='/api/query':
                        if body.get('mode','llm')=='llm':self.reserve_model_call()
                        result=service.query(body)
                    elif path in ('/api/graph','/api/graph/generate'):
                        data=generate(body) if path.endswith('/generate') else body
                        if type(data.get('nodes')) is not int or data['nodes']>500 or not isinstance(data.get('edges'),list) or len(data['edges'])>2000:raise Error('Hosted limit: 500 nodes / 2000 edges')
                        result=service.store.publish(data).public();status=201
                        with service.store.lock:
                            for v in sorted(service.store.history)[:-8]:del service.store.history[v]
                    else:raise Error('Not found',404)
        except (Error,ValueError,TypeError) as exc:
            status=getattr(exc,'status',400);result={'error':str(exc),'code':getattr(exc,'code','invalid_request')}
        except Exception:status=500;result={'error':'Internal server error'}
        finally:
            if token is not None:model_config.visitor_settings.reset(token)
            if session is not None:
                with self.lock:session['active']-=1
        if status>=400:ctype='application/json'
        raw=result if ctype=='image/png' else result.encode() if ctype.startswith('text/') else json.dumps(result,ensure_ascii=False).encode()
        headers=[('Content-Type',ctype+('' if ctype=='image/png' else '; charset=utf-8')),('Content-Length',str(len(raw))),('Cache-Control','private, max-age=3600' if ctype=='image/png' and status==200 else 'no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','same-origin')]
        route=path if path in ('/','/api/navigation','/api/query','/healthz','/readyz','/metrics') else 'other'
        telemetry.http.labels(route,str(status)).observe(time.monotonic()-started)
        telemetry.log('http',route=route,status=status,duration_ms=round((time.monotonic()-started)*1000,2))
        telemetry.request_id.reset(ctx)
        headers.append(('X-Request-ID',rid))
        if sid:headers.append(('Set-Cookie',f'graph_session={sid}; Path=/; HttpOnly; SameSite=Strict'+('; Secure' if self.origin.startswith('https://') else '')))
        if status in (429,503):headers.append(('Retry-After','60'))
        from http import HTTPStatus
        start_response(f'{status} {HTTPStatus(status).phrase}',headers);return [raw]

if __name__=='__main__':
    from waitress import serve
    serve(HostedApp(),host='0.0.0.0',port=int(os.getenv('PORT','10000')),threads=12,connection_limit=64,max_request_body_size=200000,channel_timeout=60,expose_tracebacks=False)
