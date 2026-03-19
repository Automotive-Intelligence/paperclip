import unittest
from fastapi.testclient import TestClient

import app
from tools import email_engine


class FoundationEndpointsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)

    def test_health_has_foundation_fields(self):
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('status', body)
        self.assertIn('environment', body)
        self.assertIn('strict_startup', body)
        self.assertIn('llm_model', body)
        self.assertIn('llm_ready', body)

    def test_readiness_contract(self):
        resp = self.client.get('/health/ready')
        self.assertIn(resp.status_code, (200, 503))
        body = resp.json()
        self.assertIn('ready', body)
        self.assertIn('warnings', body)
        self.assertIn('fatals', body)


class ParsingFallbackTests(unittest.TestCase):
    def test_parse_prospects_uses_heuristic_when_llm_fails(self):
        raw = (
            '1. Business Name: One Hour HVAC, Type: HVAC, City: Dallas, '
            'Reason for targeting: Missed calls after-hours\n'
            'Subject: missed calls\n'
            'Body: Quick note - we can capture calls after-hours.\n'
            'Follow-up angle for touch 2: We can also book appointments automatically.\n'
        )

        original = email_engine._call_parser_llm
        try:
            def boom(_prompt: str, max_tokens: int = 3000):
                raise RuntimeError('forced parser failure')

            email_engine._call_parser_llm = boom
            prospects = email_engine.parse_prospects(raw, agent_name='tyler')
            self.assertGreaterEqual(len(prospects), 1)
            self.assertEqual(prospects[0].get('business_name'), 'One Hour HVAC')
            self.assertEqual(prospects[0].get('city'), 'Dallas')
        finally:
            email_engine._call_parser_llm = original


if __name__ == '__main__':
    unittest.main()
