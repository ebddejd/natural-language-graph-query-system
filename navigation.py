"""Map-first navigation: explicit coordinates, place selection, real route variants."""
import copy, json, math, secrets, threading, time
from core import Error
from walking import inside, distance
from transit import TransitPlanner, departure
import journey_agent as agent

def points(route):
    g=route['geometry'];return [p for line in g['coordinates'] for p in line] if g['type']=='MultiLineString' else g['coordinates']
def edges(route):
    ps=[tuple(round(v,5) for v in p) for p in points(route)]
    return {tuple(sorted((a,b))) for a,b in zip(ps,ps[1:]) if a!=b}
def distinct(route,others):
    a=edges(route)
    return bool(a) and all(len(a & edges(b))/max(1,min(len(a),len(edges(b))))<.90 for b in others)
class NavigationSession:
    def __init__(self,provider):self.provider=provider;self.lock=threading.Lock();self.state={};self.revision=0
    def public(self,message=None):
        return dict(revision=self.revision,mode=self.state.get('mode','walk'),depart_at=self.state.get('depart_at',''),message=message or self.state.get('message','Describe your journey and requirements. I will clarify, plan and check the options. You can also choose endpoints on the map.'),**{k:self.state.get(k) for k in ('origin','destination','pending','routes','selected','trace','chat','agent_plan','agent_active','model_usage')})
    def handle(self,b,reserve=lambda:None):
        if not self.lock.acquire(False):raise Error('A route request is already running. Please wait.',409)
        try:
            if b.get('revision')!=self.revision:raise Error('Page state changed. Please refresh and retry.',409)
            old=copy.deepcopy(self.state)
            try:self.action(b,reserve)
            except Exception:self.state=old;raise
            self.revision+=1;return self.public()
        finally:self.lock.release()
    def invalidate(self):
        for k in ('routes','selected','pending','trace'):self.state.pop(k,None)
    def action(self,b,reserve):
        s=self.state;a=b.get('action')
        if a in ('point','search','swap'):s.pop('next_search',None)
        if a=='mode':
            if b.get('mode') not in ('walk','drive','transit'):raise Error('Unsupported travel mode')
            s['mode']=b['mode']
            if s.get('agent_active'):s['agent_plan']['modes']=[b['mode']]
            self.invalidate();self.plan();return
        if a=='depart':
            value=b.get('depart_at') or '';departure(value);s['depart_at']=value
            if s.get('agent_active'):
                s['agent_plan'].update(time=value or 'now',time_kind='depart');self.invalidate();self.plan()
            elif s.get('mode')=='transit':self.invalidate();self.plan()
            return
        if a=='reset':self.state={};return
        if a=='select':
            if b.get('id') not in [r['id'] for r in s.get('routes',[])]:raise Error('This route has expired. Please plan again.')
            s['selected']=b['id'];return
        if a=='swap':
            s['origin'],s['destination']=s.get('destination'),s.get('origin');self.invalidate();self.plan();return
        if a=='point':
            field=b.get('field');lon=b.get('lon');lat=b.get('lat')
            if field not in ('origin','destination') or type(lon) not in (int,float) or type(lat) not in (int,float) or not inside(lon,lat):raise Error('Please choose a point within the supported Waterloo area.')
            self.invalidate();s[field]={'id':secrets.token_hex(6),'name':('Current location ' if b.get('current_location') is True else 'Map pin ')+f'{lat:.5f}, {lon:.5f}','lon':lon,'lat':lat};self.plan();return
        if a=='search':
            field=b.get('field');text=b.get('text')
            if field not in ('origin','destination') or not isinstance(text,str) or not 1<=len(text)<=300:raise Error('Enter a valid place name.')
            self.invalidate();s.pop(field,None);self.search(field,text);return
        if a=='choose':
            pending=s.get('pending') or {};choice=next((p for p in pending.get('options',[]) if p['id']==b.get('id')),None)
            if not choice:raise Error('Select a place from the results.')
            s[pending['field']]=choice;s.pop('pending',None)
            if s.get('next_search'):
                field,text=s.pop('next_search');self.search(field,text)
            else:self.plan()
            return
        if a=='message':
            text=b.get('text')
            if not isinstance(text,str) or not 1<=len(text)<=2000:raise Error('Enter your route request.')
            reserve();r,trace=agent.extract(text,s)
            from telemetry import record_turn
            record_turn(s,trace)
            newplan=agent.merge(s.get('agent_plan'),r)
            only_select=r['select_option'] is not None and not r['clear'] and not any(r.get(k) is not None for k in agent.DEFAULT) and not r['origin'] and not r['destination'] and not r['question']
            self.say('user',text)
            if only_select:
                options=s.get('routes',[]);i=r['select_option']-1
                if i>=len(options):raise Error('That option does not exist. Choose one of the displayed routes.')
                s['selected']=options[i]['id'];self.say('assistant','Selected option '+str(i+1)+'. Its directions are shown on the map.');return
            searches=s.pop('agent_searches',[])
            if s.get('pending',{}).get('query'):searches.append((s['pending']['field'],s['pending']['query']))
            if s.get('next_search'):searches.append(s.pop('next_search'))
            self.invalidate();s['agent_active']=True;s['agent_plan']=newplan;s['agent_trace']=[trace]
            s['mode']=newplan['modes'][0]
            for f in ('origin','destination'):
                if r[f]:
                    searches=[(k,v) for k,v in searches if k!=f];searches.append((f,r[f]));s.pop(f,None)
            if r['question']:
                s['agent_searches']=searches;s['agent_question']=r['question'];self.say('assistant',r['question']);return
            s.pop('agent_question',None)
            if len(searches)>1:s['next_search']=searches[1]
            if searches:self.search(*searches[0]);self.say('assistant',s['message'])
            else:self.plan()
            return
        if a=='plan':self.plan();return
        raise Error('Unsupported action')
    def say(self,role,text):
        s=self.state;s.setdefault('chat',[]).append({'role':role,'text':text});s['chat']=s['chat'][-20:]
        if role=='assistant':s['message']=text
    def search(self,field,text):
        options=self.provider.geocode(text)
        if self.state.get('agent_active'):self.state.setdefault('agent_trace',[]).append({'tool':'search_'+field,'matches':len(options)})
        for i,p in enumerate(options):p['id']=p['id'] or str(i)
        self.state['pending']={'field':field,'query':text,'options':options};self.state['message']='Please confirm the '+('origin' if field=='origin' else 'destination')+'.' if options else 'No place found. Try another name or select a point on the map.'
    def plan(self):
        s=self.state;s.pop('pending',None)
        if s.get('agent_active') and s.get('agent_question'):
            s['message']=s['agent_question'];return
        if not s.get('origin') or not s.get('destination'):
            s['message']='Please set the '+('origin' if not s.get('origin') else 'destination')+'.'
            if s.get('agent_active'):self.say('assistant',s['message'])
            return
        a,b=s['origin'],s['destination']
        if distance((a['lon'],a['lat']),(b['lon'],b['lat']))<10:raise Error('Choose endpoints at least 10 metres apart.')
        if s.get('agent_active'):
            routes,trace,message=agent.execute(self.provider,a,b,s['agent_plan'])
            for i,r in enumerate(routes):r.update(id=secrets.token_hex(8),color=['#2563eb','#e07b16','#8b46cf'][i],recommended=i==0)
            s['routes']=routes;s['selected']=routes[0]['id'] if routes else None;s['trace']=s.get('agent_trace',[])+trace
            self.say('assistant',message);return
        if s.get('mode')=='transit':
            try:routes=TransitPlanner(self.provider).plan(a,b,s.get('depart_at'))
            except Error as exc:
                s['routes']=[];s['selected']=None;s['trace']=[];s['message']=str(exc);return
            for i,r in enumerate(routes):r.update(id=secrets.token_hex(8),color=['#2563eb','#e07b16','#8b46cf'][i],recommended=i==0)
            s['routes']=routes;s['selected']=routes[0]['id'];s['trace']=[{'source':'GRT official GTFS','schedule_downloaded':routes[0]['data_downloaded'],'service_ranges':routes[0]['service_ranges'],'search':'Up to 2 transfers, 4-hour window, nearby walkable stops'}]
            s['message']=f'{len(routes)} scheduled itinerary(s) found. Earliest arrival is recommended. Times are scheduled, not live. Select an itinerary for boarding and transfer instructions.'
            return
        started=time.monotonic();mode=s.get('mode','walk')
        try:base=self.provider.route([a,b],mode=mode)
        except Error as exc:
            if mode!='transit':raise
            s['routes']=[];s['selected']=None;s['trace']=[]
            s['message']='Public transit routing is unavailable for these endpoints. Try different stops or check current services in Google Maps. '+str(exc)
            return
        routes=[base];trace=[{'strategy':'short','mode':mode,'distance_m':base['distance_m']}]
        if base['distance_m']>15000:raise Error('This preview supports routes up to 15 km.',422)
        ps=points(base);partial=False
        attempts=[] if mode=='transit' else [('balanced',None),('short',ps[len(ps)//2]),('short',ps[len(ps)//3])]
        for kind,avoid in attempts:
            if len(routes)>=3:break
            if time.monotonic()-started>32:partial=True;break
            try:r=self.provider.route([a,b],route_type=kind,avoid=avoid,mode=mode)
            except Error:partial=True;continue
            accepted=r['distance_m']<=min(15000,base['distance_m']*1.8+150) and distinct(r,routes)
            trace.append({'strategy':kind if not avoid else 'avoid_midroute_location','distance_m':r['distance_m'],'distinct_accepted':accepted})
            if accepted:routes.append(r)
        routes.sort(key=lambda r:r['distance_m'])
        for i,r in enumerate(routes):r.update(id=secrets.token_hex(8),color=['#2563eb','#e07b16','#8b46cf'][i],recommended=i==0)
        s['routes']=routes;s['selected']=routes[0]['id'];s['trace']=trace
        s['message']=f'{len(routes)} distinct route(s) found. The shortest available option is recommended. '+('Some alternatives could not be checked. ' if partial else '')+('No duplicate routes are shown.' if len(routes)==1 else 'Select a route or card to compare.')
        if mode=='transit':
            if not base.get('has_transit'):
                s['message']='The transit provider returned a walking-only option, not a confirmed bus or rail itinerary. Use the Google Maps link to check current transit services.'
                for r in routes:r['walking_only']=True
            else:s['message']+=' Transit coverage depends on provider data; live departures and service disruptions are not verified.'
