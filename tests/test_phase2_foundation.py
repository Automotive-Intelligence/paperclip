import unittest
from unittest.mock import patch
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

    def test_sales_preflight_contract(self):
        resp = self.client.get('/api/sales/preflight')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('overall_ready', body)
        self.assertIn('by_provider', body)
        self.assertIn('sales_agents', body)
        self.assertIn('ghl', body['by_provider'])
        self.assertIn('hubspot', body['by_provider'])
        self.assertIn('attio', body['by_provider'])
        self.assertEqual(len(body['sales_agents']), 3)


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

    @patch('app.track_event')
    @patch('app.push_prospects_to_crm')
    @patch('app.parse_prospects')
    @patch('app._crm_ready_for', return_value=True)
    def test_sales_pipeline_hubspot_marks_email_not_supported(
        self,
        _mock_ready,
        mock_parse,
        mock_push,
        _mock_track,
    ):
        mock_parse.return_value = [
            {
                'business_name': 'Alpha Motors',
                'city': 'Dallas',
                'business_type': 'Auto Dealer',
                'reason': 'AI readiness gap',
            }
        ]
        mock_push.return_value = (
            'hubspot',
            [
                {
                    'business_name': 'Alpha Motors',
                    'status': 'created',
                    'contact_id': 'hs-1',
                    'email_sent': False,
                }
            ],
        )

        result = app._execute_sales_pipeline('ryan_data', 'output', 'autointelligence')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['crm_provider'], 'hubspot')
        self.assertEqual(result['crm_created'], 1)
        self.assertEqual(result['emails_sent'], 0)
        self.assertEqual(result['provider_not_email_capable'], 1)
        self.assertIn('email_not_supported_provider:hubspot', result['email_capability_reason'])


if __name__ == '__main__':
    unittest.main()
