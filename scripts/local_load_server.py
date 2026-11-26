"""Loopback-only HTTP load fixture. Never uses live provider credentials."""
from pathlib import Path
import os
import sys
import time
from unittest.mock import patch
import json

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hosted import HostedApp
from waitress import serve
from journey_agent import FIELDS


def completion(*args):
    time.sleep(.03)
    values=dict.fromkeys(FIELDS)
    values.update(clear=[],fallback_drive_over_minutes=45)
    return {'tool_calls':[{'function':{'name':'update_journey','arguments':json.dumps(values)}}]}, {}


if __name__=='__main__':
    port=int(os.environ.get('LOAD_PORT','18926'))
    app=HostedApp(os.getenv('PUBLIC_URL',f'http://127.0.0.1:{port}'))
    app.model_limit=10000
    with patch('journey_agent.completion',completion):
        serve(app,host=os.getenv('LOAD_BIND','127.0.0.1'),port=port,threads=12)
