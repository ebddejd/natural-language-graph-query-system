import json
import os
import unittest
from unittest.mock import Mock

from core import Error
from shared_journey import SharedJourney


@unittest.skipUnless(os.getenv('TEST_REDIS_URL'), 'requires disposable Redis')
class ReplicaTests(unittest.TestCase):
    def test_destination_survives_backend_replacement_and_old_revision_is_rejected(self):
        first=SharedJourney(os.environ['TEST_REDIS_URL'])
        second=SharedJourney(os.environ['TEST_REDIS_URL'])
        sid=first.identify('')
        provider=Mock()
        state=first.update(sid,provider,dict(action='point',field='destination',lat=43.49,lon=-80.52,revision=0),lambda:None)
        self.assertEqual(second.identify(sid),sid)
        restored=second.read(sid,provider)
        self.assertEqual(restored['destination'],state['destination'])
        with self.assertRaises(Error):
            second.update(sid,provider,dict(action='reset',revision=0),lambda:None)
        self.assertEqual(second.read(sid,provider)['revision'],1)
        first.client.delete('journey:'+sid)
