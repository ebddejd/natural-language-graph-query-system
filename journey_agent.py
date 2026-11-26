"""LLM-guided, bounded journey workflow with deterministic evidence checks."""
import copy,json,math,time
from datetime import datetime,timedelta
from planner import completion
from core import Error
from transit import TransitPlanner,departure,now_local,TZ,stamp

from pathlib import Path

PROMPT_VERSION='journey-v1'
PROMPT=(Path(__file__).parent/'prompts'/(PROMPT_VERSION+'.txt')).read_text().strip()

DEFAULT={'modes':['transit'],'time_kind':'depart','time':'now','max_walk_minutes':None,'max_transfers':2,'avoid_rail':False,'preference':'fastest','fallback_drive_over_minutes':None}
FIELDS={'origin':{'type':['string','null']},'destination':{'type':['string','null']},'modes':{'type':['array','null'],'items':{'type':'string','enum':['walk','drive','transit']}},'time_kind':{'type':['string','null'],'enum':['depart','arrive',None]},'time':{'type':['string','null']},'max_walk_minutes':{'type':['number','null']},'max_transfers':{'type':['integer','null']},'avoid_rail':{'type':['boolean','null']},'preference':{'type':['string','null'],'enum':['fastest','least_walking','fewest_transfers','shortest',None]},'fallback_drive_over_minutes':{'type':['number','null']},'clear':{'type':'array','items':{'type':'string','enum':list(DEFAULT)}},'select_option':{'type':['integer','null']},'question':{'type':['string','null']}}
TOOL={'type':'function','function':{'name':'update_journey','description':'Update explicit journey requirements, select an existing option, or ask a necessary clarification. Server tools execute and verify the requested plan.','parameters':{'type':'object','properties':FIELDS,'required':list(FIELDS),'additionalProperties':False}}}

def _extract(text,state):
    def name(p):
        if not p:return None
        n=p.get('name','')
        return 'Confirmed map location' if n.startswith(('Current location','Map pin')) else n
    context={'now_waterloo':now_local().isoformat(),'plan':state.get('agent_plan',DEFAULT),'origin':name(state.get('origin')),'destination':name(state.get('destination')),'selected_option':next((i+1 for i,r in enumerate(state.get('routes',[])) if r['id']==state.get('selected')),None),'options':[{'option':i+1,'mode':r.get('travel_mode'),'minutes':round(r['duration_s']/60,1),'transfers':r.get('transfers',0)} for i,r in enumerate(state.get('routes',[]))],'pending':state.get('agent_question')}
    prompt=PROMPT+' '+json.dumps(context)
    history=[{'role':m['role'],'content':m['text']} for m in state.get('chat',[])[-8:]]
    t=time.monotonic();message,usage=completion([{'role':'system','content':prompt}]+history+[{'role':'user','content':text}],[TOOL])
    try:
        calls=message['tool_calls'];assert len(calls)==1 and calls[0]['function']['name']=='update_journey'
        args=json.loads(calls[0]['function']['arguments']);validate(args)
    except (AssertionError,KeyError,TypeError,ValueError):raise Error('The AI returned an invalid planning request. Please rephrase; your previous plan was kept.',502)
    return args,{'tool':'understand_request','prompt_version':PROMPT_VERSION,'model_ms':round((time.monotonic()-t)*1000),'usage':usage}

def extract(text,state):
    from telemetry import model_event
    start=time.monotonic()
    try:
        args,trace=_extract(text,state)
    except Exception:
        model_event(PROMPT_VERSION,start,'error',{})
        raise
    model_event(PROMPT_VERSION,start,'success',trace['usage'])
    return args,trace

def validate(a):
    if not isinstance(a,dict) or set(a)!=set(FIELDS):raise ValueError()
    for k in ('origin','destination','question'):
        if a[k] is not None and (not isinstance(a[k],str) or not 1<=len(a[k])<=500):raise ValueError()
    if a['modes'] is not None and (not isinstance(a['modes'],list) or not 1<=len(a['modes'])<=3 or any(x not in ('walk','drive','transit') for x in a['modes'])):raise ValueError()
    if not isinstance(a['clear'],list) or any(k not in DEFAULT for k in a['clear']):raise ValueError()
    if a['time_kind'] not in (None,'depart','arrive') or a['preference'] not in (None,'fastest','least_walking','fewest_transfers','shortest'):raise ValueError()
    if a['time'] is not None and (not isinstance(a['time'],str) or len(a['time'])>32):raise ValueError()
    for k,hi in [('max_walk_minutes',180),('fallback_drive_over_minutes',240)]:
        if a[k] is not None and (type(a[k]) not in (int,float) or not math.isfinite(a[k]) or not 0<=a[k]<=hi):raise ValueError()
    if a['max_transfers'] is not None and (type(a['max_transfers']) is not int or not 0<=a['max_transfers']<=2):raise ValueError()
    if a['avoid_rail'] is not None and type(a['avoid_rail']) is not bool:raise ValueError()
    if a['select_option'] is not None and (type(a['select_option']) is not int or not 1<=a['select_option']<=3):raise ValueError()

def merge(previous,args):
    p=copy.deepcopy(previous or DEFAULT)
    for k in args['clear']:p[k]=copy.deepcopy(DEFAULT[k])
    for k in DEFAULT:
        if args.get(k) is not None:p[k]=copy.deepcopy(args[k])
    p['modes']=list(dict.fromkeys(p['modes']))
    if p['time']!='now':departure(p['time'])
    if p['time_kind']=='arrive' and p['time']=='now':raise Error('Please provide a future arrival deadline.')
    return p

def evidence(route,p):
    walk=route.get('walk_seconds',route['duration_s'] if route.get('travel_mode')=='walk' else 0)
    checks=[]
    if p['max_walk_minutes'] is not None:checks.append({'requirement':'Total walking','actual':round(walk/60,1),'limit':p['max_walk_minutes'],'unit':'min','passed':walk<=p['max_walk_minutes']*60})
    checks.append({'requirement':'Transfers','actual':route.get('transfers',0),'limit':p['max_transfers'],'passed':route.get('transfers',0)<=p['max_transfers']})
    if p['avoid_rail']:checks.append({'requirement':'No light rail','passed':not any(l.get('vehicle')=='ION light rail' for l in route.get('legs',[]))})
    if p['time_kind']=='arrive':checks.append({'requirement':'Arrival deadline','actual':route['arrival'],'limit':departure(p['time']).isoformat(),'passed':datetime.fromisoformat(route['arrival'])<=departure(p['time'])})
    return checks

def execute(provider,a,b,p):
    trace=[];candidates=[];errors=[]
    at=departure(None if p['time']=='now' else p['time']);arrive=p['time_kind']=='arrive'
    def run(mode):
        t=time.monotonic()
        try:
            if mode=='transit':
                results=TransitPlanner(provider).plan(a,b,None if p['time']=='now' else p['time'],constraints=p,arrive_by=arrive)
            else:
                r=provider.route([a,b],mode=mode,route_type='short' if p['preference']=='shortest' else 'balanced')
                if r['distance_m']>15000:raise Error('Walking/driving routes are limited to 15 km.',422)
                start=at-timedelta(seconds=r['duration_s']) if arrive else at
                if arrive and at>now_local() and start<now_local():raise Error('The arrival deadline is too soon for this mode.',422)
                r.update(departure=start.isoformat(),arrival=(start+timedelta(seconds=r['duration_s'])).isoformat(),transfers=0)
                results=[r]
            accepted=0
            for r in results:
                r['travel_mode']=mode;r['walk_seconds']=sum(l['duration_s'] for l in r.get('legs',[]) if l['kind']=='walk') if mode=='transit' else r['duration_s'] if mode=='walk' else 0
                checks=evidence(r,p);r['checks']=checks
                if all(c['passed'] for c in checks):candidates.append(r);accepted+=1
            trace.append({'tool':'plan_'+mode,'duration_ms':round((time.monotonic()-t)*1000),'returned':len(results),'constraints_passed':accepted})
        except Error as e:errors.append(mode+': '+str(e));trace.append({'tool':'plan_'+mode,'error':str(e)})
    modes=list(p['modes']);threshold=p['fallback_drive_over_minutes']
    conditional=threshold is not None and 'transit' in modes
    for mode in modes:
        if mode=='drive' and conditional:continue
        run(mode)
    if conditional:
        ts=[r for r in candidates if r['travel_mode']=='transit']
        trigger=not ts or min(r['duration_s'] for r in ts)>threshold*60
        trace.append({'tool':'evaluate_driving_fallback','threshold_minutes':threshold,'triggered':trigger})
        if trigger:run('drive')
    rank={'least_walking':lambda r:(r['walk_seconds'],r['duration_s']),'fewest_transfers':lambda r:(r.get('transfers',0),r['duration_s']),'shortest':lambda r:(r['distance_m'],r['duration_s']),'fastest':lambda r:(r['duration_s'],r['walk_seconds'])}[p['preference']]
    candidates.sort(key=rank)
    # Keep the best candidate for every compared mode, then fill remaining slots.
    chosen=[];seen=set()
    for r in candidates:
        if r['travel_mode'] not in seen:chosen.append(r);seen.add(r['travel_mode'])
    for r in candidates:
        if len(chosen)>=3:break
        if not any(r is x for x in chosen):chosen.append(r)
    chosen.sort(key=rank);chosen=chosen[:3]
    if not chosen:
        message='No verified candidate met all your requirements in this bounded search. I kept your constraints unchanged. You can increase the walking limit, allow another transfer, change the time, or compare driving.'
        if errors:message+=' '+ '; '.join(errors)
    else:
        best=chosen[0];message=f"Option 1 is recommended for your '{p['preference']}' preference: {best['travel_mode']}, {math.ceil(best['duration_s']/60)} min total, {math.ceil(best['walk_seconds']/60)} min walking, {best.get('transfers',0)} transfer(s). All displayed options pass the checked constraints."
        if arrive:message+=' Leave by '+datetime.fromisoformat(best['departure']).strftime('%H:%M')+' Waterloo time to meet the requested arrival deadline.'
        if any(r['travel_mode']=='transit' for r in chosen):message+=' Transit times are scheduled, not live.'
        if any(r['travel_mode']=='drive' for r in chosen):message+=' Driving estimates exclude live traffic, parking and walking from parking.'
        if errors:message+=' Some tools could not return a result: '+'; '.join(errors)
    from telemetry import tool
    for step in trace:
        tool.labels(step['tool'],'error' if 'error' in step else 'success').inc()
    return chosen,trace,message
