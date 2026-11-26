"""Persist navigation conversations in Redis. WATCH rejects concurrent stale commits."""
import json
import re
import secrets
from redis import Redis, WatchError
from core import Error
from navigation import NavigationSession

TTL = 1800


class SharedJourney:
    def __init__(self, url):
        self.client = Redis.from_url(url,decode_responses=True,socket_timeout=3,socket_connect_timeout=3)

    def allow_model(self, limit):
        # A shared fixed window prevents scale-out from multiplying provider spend.
        count=self.client.eval("""
          local n=redis.call('INCR',KEYS[1])
          if n==1 then redis.call('EXPIRE',KEYS[1],60) end
          return n
        """,1,'quota:journey:model')
        if count>limit:
            raise Error('Too many AI requests. Please try again shortly.',429)

    def identify(self, candidate):
        if re.fullmatch(r'[A-Za-z0-9_-]{43}',candidate) and self.client.exists('journey:'+candidate):
            return candidate
        while True:
            sid = secrets.token_urlsafe(32)
            if self.client.set('journey:'+sid,'{"revision":0,"state":{}}',ex=TTL,nx=True):
                return sid

    def restore(self, raw, provider):
        if raw is None:
            raise Error('Session expired. Reload to start again.',409)
        value=json.loads(raw)
        session=NavigationSession(provider)
        session.revision=value['revision']
        session.state=value['state']
        return session

    def read(self, sid, provider):
        return self.restore(self.client.get('journey:'+sid),provider).public()

    def update(self, sid, provider, body, reserve):
        key='journey:'+sid
        with self.client.pipeline() as pipe:
            try:
                pipe.watch(key)
                session=self.restore(pipe.get(key),provider)
                result=session.handle(body,reserve)
                pipe.multi()
                pipe.set(key,json.dumps(dict(revision=session.revision,state=session.state)),ex=TTL)
                pipe.execute()
                return result
            except WatchError:
                raise Error('Page state changed. Refresh before retrying.',409)
