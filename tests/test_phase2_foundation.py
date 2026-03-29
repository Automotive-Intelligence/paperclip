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

    @patch('app.ghl_site_publish_ready', return_value=False)
    def test_ghl_content_publish_requires_config(self, _mock_ready):
        resp = self.client.post('/content/publish/ghl')
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn('detail', body)

    @patch('app.track_event')
    @patch('app.mark_content_published')
    @patch('app.publish_content_to_ghl_site')
    @patch('app.get_content_queue')
    @patch('app.ghl_site_publish_ready', return_value=True)
    def test_ghl_content_publish_contract(
        self,
        _mock_ready,
        mock_queue,
        mock_publish,
        mock_mark,
        _mock_track,
    ):
        mock_queue.return_value = [
            {
                'id': 42,
                'business_key': 'aiphoneguy',
                'agent_name': 'zoe',
                'platform': 'website',
                'content_type': 'blog',
                'title': 'Missed Calls Cost Revenue',
                'body': 'Every missed call is a lost opportunity.',
                'hashtags': '#dfw #aiphoneguy',
                'cta': 'Book a demo',
                'funnel_stage': 'awareness',
                'created_at': '2026-03-19T08:00:00Z',
            }
        ]
        mock_publish.return_value = {
            'status': 'published',
            'slug': 'missed-calls-cost-revenue',
            'url': 'https://example.com/blog/missed-calls-cost-revenue',
            'provider': 'ghl_webhook',
        }

        resp = self.client.post('/content/publish/ghl?limit=5')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['published'], 1)
        self.assertEqual(body['failed'], 0)
        self.assertEqual(len(body['results']), 1)
        self.assertEqual(body['results'][0]['status'], 'published')
        mock_mark.assert_called_once_with(42)

    @patch('app.ghl_social_publish_ready', return_value=False)
    def test_ghl_social_publish_requires_config(self, _mock_ready):
        resp = self.client.post('/content/publish/ghl/social')
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn('detail', body)

    @patch('app.track_event')
    @patch('app.mark_content_published')
    @patch('app.publish_content_to_ghl_social')
    @patch('app.get_content_queue')
    @patch('app.ghl_social_publish_ready', return_value=True)
    def test_ghl_social_publish_contract(
        self,
        _mock_ready,
        mock_queue,
        mock_publish,
        mock_mark,
        _mock_track,
    ):
        mock_queue.return_value = [
            {
                'id': 77,
                'business_key': 'aiphoneguy',
                'agent_name': 'zoe',
                'platform': 'linkedin',
                'content_type': 'post',
                'title': '3 Ways To Stop Missing Calls',
                'body': 'Quick post body',
                'hashtags': '#aiphoneguy',
                'cta': 'DM us',
                'funnel_stage': 'awareness',
                'created_at': '2026-03-19T08:00:00Z',
            },
            {
                'id': 78,
                'business_key': 'aiphoneguy',
                'agent_name': 'zoe',
                'platform': 'blog',
                'content_type': 'article',
                'title': 'Long Form Blog',
                'body': 'Blog body',
                'hashtags': '',
                'cta': 'Book demo',
                'funnel_stage': 'consideration',
                'created_at': '2026-03-19T08:00:00Z',
            },
        ]
        mock_publish.return_value = {
            'status': 'published',
            'url': 'https://example.com/social/77',
            'provider': 'ghl_social_webhook',
        }

        resp = self.client.post('/content/publish/ghl/social?limit=5')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['published'], 1)
        self.assertEqual(body['failed'], 0)
        self.assertEqual(len(body['results']), 1)
        self.assertEqual(body['results'][0]['platform'], 'linkedin')
        mock_mark.assert_called_once_with(77)

    @patch('app.publish_content_to_ghl')
    @patch('app.publish_content_to_ghl_social_endpoint')
    def test_ghl_publish_all_contract(self, mock_social, mock_site):
        mock_site.return_value = {'status': 'ok', 'published': 1, 'failed': 0, 'results': []}
        mock_social.return_value = {'status': 'ok', 'published': 2, 'failed': 0, 'results': []}

        resp = self.client.post('/content/publish/ghl/all?limit_site=1&limit_social=2')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['status'], 'ok')
        self.assertIn('site', body)
        self.assertIn('social', body)
        self.assertEqual(body['site']['published'], 1)
        self.assertEqual(body['social']['published'], 2)

    @patch('app.ghost_publish_ready', return_value=False)
    def test_ghost_publish_requires_config(self, _mock_ready):
        resp = self.client.post('/content/publish/ghost/callingdigital')
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn('detail', body)

    @patch('app.track_event')
    @patch('app.mark_content_published')
    @patch('app.publish_content_to_ghost')
    @patch('app.get_content_queue')
    @patch('app.ghost_publish_ready', return_value=True)
    def test_ghost_publish_contract(
        self,
        _mock_ready,
        mock_queue,
        mock_publish,
        mock_mark,
        _mock_track,
    ):
        mock_queue.return_value = [
            {
                'id': 91,
                'business_key': 'callingdigital',
                'agent_name': 'sofia',
                'platform': 'blog',
                'content_type': 'article',
                'title': 'Why Service Businesses Need Better Websites',
                'body': 'Your website is often your first salesperson.',
                'hashtags': '#marketing #webdesign',
                'cta': 'Book a strategy call',
                'funnel_stage': 'consideration',
                'created_at': '2026-03-28T08:00:00Z',
            },
            {
                'id': 92,
                'business_key': 'callingdigital',
                'agent_name': 'sofia',
                'platform': 'linkedin',
                'content_type': 'post',
                'title': 'Short social post',
                'body': 'Social body',
                'hashtags': '',
                'cta': '',
                'funnel_stage': 'awareness',
                'created_at': '2026-03-28T08:00:00Z',
            },
        ]
        mock_publish.return_value = {
            'status': 'published',
            'slug': 'why-service-businesses-need-better-websites',
            'url': 'https://blog.calling.digital/why-service-businesses-need-better-websites/',
            'provider': 'ghost',
        }

        resp = self.client.post('/content/publish/ghost/callingdigital?limit=5')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['published'], 1)
        self.assertEqual(body['failed'], 0)
        self.assertEqual(len(body['results']), 1)
        self.assertEqual(body['results'][0]['status'], 'published')
        mock_mark.assert_called_once_with(91)


class ParsingFallbackTests(unittest.TestCase):
    @patch('app.track_event')
    @patch('app.queue_content')
    @patch('app.parse_content_pieces')
    def test_callingdigital_content_pipeline_normalizes_brand_and_links(
        self,
        mock_parse,
        mock_queue,
        _mock_track,
    ):
        mock_parse.return_value = [
            {
                'platform': 'blog',
                'content_type': 'article',
                'title': 'Why Nova AI Consulting Matters',
                'body': 'Nova AI Consulting helps firms modernize marketing. Learn more here [Link]',
                'hashtags': '',
                'cta': 'Book a strategy call → [Link]',
                'funnel_stage': 'consideration',
            }
        ]
        mock_queue.return_value = 1

        result = app._execute_content_pipeline('sofia', 'raw', 'callingdigital')

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['queued'], 1)
        queued_pieces = mock_queue.call_args[0][2]
        self.assertEqual(queued_pieces[0]['title'], 'Why Calling Digital Matters')
        self.assertIn('Calling Digital helps firms modernize marketing.', queued_pieces[0]['body'])
        self.assertIn('https://calling.digital', queued_pieces[0]['body'])
        self.assertIn('https://calling.digital', queued_pieces[0]['cta'])

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
