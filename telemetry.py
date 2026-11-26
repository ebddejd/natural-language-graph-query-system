"""Bounded-cardinality metrics; never log request bodies, locations or credentials."""
from contextvars import ContextVar
import json
import os
import time

from prometheus_client import Counter, Histogram

request_id = ContextVar('journey_request_id', default=None)
http = Histogram('journey_http_seconds','HTTP request duration',['route','status'])
llm = Histogram('journey_model_seconds','Journey extractor duration',['prompt','outcome'])
tokens = Counter('journey_llm_tokens','Reported model tokens',['kind'])
cost = Counter('journey_llm_estimated_usd','Estimated model cost with configured rates')
tool = Counter('journey_tool_calls','Executed routing tools',['name','outcome'])
maps = Counter('journey_map_requests','Map provider requests including cache hits',['route','outcome'])


def log(event, **fields):
    print(json.dumps(dict(event=event,request_id=request_id.get(),**fields)),flush=True)


def model_event(version, start, outcome, usage):
    elapsed = time.monotonic()-start
    llm.labels(version,outcome).observe(elapsed)
    inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
    estimate=estimate_usd(usage)
    if isinstance(inp,int) and isinstance(out,int):
        tokens.labels('input').inc(inp)
        tokens.labels('output').inc(out)
    if estimate is not None:
        cost.inc(estimate)
    log('model', prompt=version, tool='update_journey',outcome=outcome,
        elapsed_ms=round(elapsed*1000,2),input_tokens=inp,output_tokens=out,estimated_usd=estimate)

def estimate_usd(usage):
    try:
        inp,out=usage['prompt_tokens'],usage['completion_tokens']
        cached=(usage.get('prompt_tokens_details') or {}).get('cached_tokens',0)
        return ((inp-cached)*float(os.environ['INPUT_USD_PER_M'])+
                (cached*float(os.environ['CACHED_USD_PER_M']) if cached else 0)+
                out*float(os.environ['OUTPUT_USD_PER_M']))/1e6
    except (ValueError,KeyError,TypeError):
        return None


def record_turn(state, trace):
    """Include cumulative measured usage in this conversation's private state."""
    usage=trace.get('usage') or {}
    total=state.setdefault('model_usage',dict(turns=0,input_tokens=0,output_tokens=0,
                                              unmetered_turns=0,unpriced_turns=0,known_cost_usd=0))
    total['turns']+=1
    if 'prompt_tokens' in usage and 'completion_tokens' in usage:
        total['input_tokens']+=usage['prompt_tokens']
        total['output_tokens']+=usage['completion_tokens']
    else:
        total['unmetered_turns']+=1
    estimate=estimate_usd(usage)
    if estimate is None:
        total['unpriced_turns']+=1
    else:
        total['known_cost_usd']+=estimate
