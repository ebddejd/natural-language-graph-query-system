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
    cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens',0)
    estimate = None
    if isinstance(inp,int) and isinstance(out,int):
        tokens.labels('input').inc(inp)
        tokens.labels('output').inc(out)
        try:
            estimate = ((inp-cached)*float(os.environ['INPUT_USD_PER_M'])+
                        (cached*float(os.environ['CACHED_USD_PER_M']) if cached else 0)+
                        out*float(os.environ['OUTPUT_USD_PER_M']))/1e6
            cost.inc(estimate)
        except (ValueError, KeyError):
            estimate = None
    log('model', prompt=version, tool='update_journey',outcome=outcome,
        elapsed_ms=round(elapsed*1000,2),input_tokens=inp,output_tokens=out,estimated_usd=estimate)
