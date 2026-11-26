"""Run the versioned journey extractor on labeled requests using the configured provider."""
import argparse
import json
import os
from collections import Counter
from datetime import datetime
from functools import partial
from pathlib import Path
from unittest.mock import patch

import journey_agent as agent
from model_config import current, public
from planner import completion

ROOT = Path(__file__).parent
CASES = json.loads((ROOT / 'data/journey-cases.json').read_text())
CLOCK = datetime.fromisoformat('2027-05-03T08:00:00-04:00')
PLACES = {'uw': ['uw', 'university of waterloo', '滑铁卢大学'],
          'mall': ['conestoga mall'], 'library': ['waterloo public library main',
          'waterloo public library main branch', 'waterloo public library (main branch)']}


def place(name):
    if name is None:
        return None
    value = name.strip().casefold()
    return next((key for key, aliases in PLACES.items() if value in aliases), value)


def context(case):
    # An explicitly supplied journey starts without previously confirmed endpoints.
    explicit = case['id'] in (1, 2, 3, 4, 10, 28)
    state = {} if explicit else {'origin': {'name': 'UW'}, 'destination': {'name': 'Conestoga Mall'}}
    if case.get('prior'):
        previous = CASES[case['prior'] - 1]
        state['agent_plan'] = dict(agent.DEFAULT, **{k:v for k,v in previous['expect'].items() if k in agent.DEFAULT})
        state['chat'] = [{'role':'user', 'text':previous['text']}]
    if case['id'] == 30:
        state['routes'] = [dict(id=str(i), travel_mode='transit', duration_s=600*i) for i in range(1,4)]
    return state


def baseline(state):
    return dict(state.get('agent_plan', agent.DEFAULT),
                origin=place(state.get('origin', {}).get('name')),
                destination=place(state.get('destination', {}).get('name')),
                question=False, selection=None)


def score(case, state, args):
    expected = dict(baseline(state), **case['expect'])
    with patch('transit.now_local', return_value=CLOCK):
        actual = dict(baseline(state), **agent.merge(state.get('agent_plan'), args))
    for key in ('origin','destination'):
        if args[key]:
            actual[key] = place(args[key])
    actual['question'] = bool(args['question'])
    actual['selection'] = args['select_option']
    if case['id'] == 29 and (actual['question'] or args['select_option'] == 2):
        actual.update(selection='unavailable', question=False)
    # Both encodings request conditional driving in execute().
    if actual['fallback_drive_over_minutes'] is not None and 'transit' in actual['modes']:
        actual['modes'] = [m for m in actual['modes'] if m != 'drive']
    actual['modes'] = sorted(actual['modes'])
    expected['modes'] = sorted(expected['modes'])
    return actual, {key for key in expected if actual[key] != expected[key]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='reports/journey-live.json')
    parser.add_argument('--threshold', type=float, default=.90)
    parser.add_argument('--omit-temperature', action='store_true')
    opt = parser.parse_args()
    if not public()['configured']:
        parser.exit(2, 'Configure LLM_MODEL and provider access before running live evaluation.\n')
    temperature = None if opt.omit_temperature else 0
    rows, agreement, correct, totals = [], 0, Counter(), Counter()
    for case in CASES:
        predictions = []
        for trial in range(3):
            state = context(case)
            row = dict(case=case['id'], trial=trial+1, passed=False)
            errors = set(case['expect'])
            try:
                with patch.object(agent,'now_local',return_value=CLOCK), patch.object(agent,'completion',partial(completion,temperature=temperature)):
                    args, trace = agent.extract(case['text'],state)
                actual, errors = score(case,state,args)
                row.update(passed=not errors, prediction=actual, errors=sorted(errors), usage=trace['usage'])
                predictions.append(actual)
            except Exception as exc:
                row['error'] = type(exc).__name__
            rows.append(row)
            for key in case['expect']:
                totals[key] += 1
                correct[key] += key not in errors
        agreement += len(predictions)==3 and predictions[0]==predictions[1]==predictions[2]
    accuracy = sum(row['passed'] for row in rows)/len(rows)
    report = dict(model=current()['model'], prompt=agent.PROMPT_VERSION, temperature=temperature,
                  cases=len(CASES), accuracy=accuracy, agreement=agreement/len(CASES),
                  fields={k:dict(correct=correct[k],total=n) for k,n in totals.items()}, rows=rows)
    # Rates in USD per million tokens. Missing prices/usage must remain unknown.
    cost = 0
    for row in rows:
        u = row.get('usage',{})
        try:
            cached = u.get('prompt_tokens_details',{}).get('cached_tokens',0)
            cost += ((u['prompt_tokens']-cached)*float(os.environ['INPUT_USD_PER_M'])+
                     (cached*float(os.environ['CACHED_USD_PER_M']) if cached else 0)+
                     u['completion_tokens']*float(os.environ['OUTPUT_USD_PER_M']))/1e6
        except (TypeError, KeyError, ValueError):
            cost = None
            break
    report['estimated_usd'] = cost
    report['pricing']={k:os.getenv(k) for k in ('INPUT_USD_PER_M','OUTPUT_USD_PER_M','CACHED_USD_PER_M','RATE_DATE')}
    Path(opt.out).parent.mkdir(exist_ok=True,parents=True)
    Path(opt.out).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(f'{len(rows)} trials, accuracy {accuracy:.1%}, agreement {report["agreement"]:.1%}')
    return int(accuracy < opt.threshold)


if __name__ == '__main__':
    raise SystemExit(main())
