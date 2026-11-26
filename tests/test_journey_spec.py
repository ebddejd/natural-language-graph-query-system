import json
import unittest
from unittest.mock import Mock, patch

import journey_agent as agent
from core import Error
from journey_eval import CASES, context, score
from navigation import NavigationSession


def update(**values):
    return dict({field: None for field in agent.FIELDS}, clear=[], **values)


class JourneySpecification(unittest.TestCase):
    def test_recommendation_fixtures(self):
        scenarios = [
            ([2640], 2400, False, 'transit'), ([2700], 2400, False, 'transit'),
            ([2701], 2400, True, 'drive'), ([2760], 2400, True, 'drive'),
            ([], 2400, True, 'drive'), ([2640, 3000], 2400, False, 'transit'),
            ([2760], 3300, True, 'transit'), ([], None, True, None),
        ]
        for seconds, drive, should_query, best in scenarios:
            with self.subTest(seconds=seconds, drive=drive):
                provider = Mock()
                if drive is None:
                    provider.route.side_effect = Error('No route')
                else:
                    provider.route.return_value = dict(duration_s=drive, distance_m=5000)
                trips = [dict(duration_s=s, distance_m=6000, legs=[]) for s in seconds]
                plan = dict(agent.DEFAULT, fallback_drive_over_minutes=45)
                with patch.object(agent.TransitPlanner, 'plan', return_value=trips):
                    routes, _, _ = agent.execute(provider, {}, {}, plan)
                self.assertEqual(provider.route.called, should_query)
                self.assertEqual(routes[0]['travel_mode'] if routes else None, best)

    def test_multiturn_through_completion_and_session(self):
        provider = Mock()
        provider.route.return_value = dict(duration_s=1500, distance_m=5000)
        session = NavigationSession(provider)
        session.state.update(origin=dict(name='UW',lat=43.47,lon=-80.54),
                             destination=dict(name='Conestoga Mall', lat=43.49,lon=-80.52))
        def reply(n):
            return ({'tool_calls':[{'function': dict(name='update_journey',
                    arguments=json.dumps(update(fallback_drive_over_minutes=n)))}]}, {})
        with patch.object(agent, 'completion', side_effect=[reply(45), reply(30)]), patch.object(
                agent.TransitPlanner,'plan',side_effect=lambda *a,**kw: [dict(duration_s=2100,distance_m=6000,legs=[])]):
            for rev, text, best in [(0, 'Drive if transit exceeds 45 mins','transit'),
                                    (1,'算了，改成 30 分钟。','drive')]:
                response = session.handle(dict(action='message',revision=rev,text=text))
                self.assertEqual(response['routes'][0]['travel_mode'], best)
                self.assertEqual(response['destination']['name'], 'Conestoga Mall')

    def test_grader_rejects_invented_constraints_and_missing_endpoints(self):
        case = CASES[9]
        args = update(origin='University of Waterloo',destination='Conestoga Mall',
                      fallback_drive_over_minutes=45)
        self.assertEqual(score(case,context(case),args)[1],set())
        args.update(max_transfers=0,destination=None)
        self.assertEqual(score(case,context(case),args)[1],{'max_transfers','destination'})

    def test_future_clock_used_by_grader(self):
        case = CASES[4]
        self.assertFalse(score(case,context(case),update(time='2027-05-04T09:00',time_kind='arrive'))[1])
