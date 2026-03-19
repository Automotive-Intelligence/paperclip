import os
import json
import unittest

from config import runtime


class CrmRoutingConfigTests(unittest.TestCase):
    def setUp(self):
        self._backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._backup)
        runtime.get_settings.cache_clear()

    def test_default_business_crm_map(self):
        os.environ.pop('BUSINESS_CRM_MAP', None)
        os.environ.pop('AGENT_CRM_MAP', None)
        runtime.get_settings.cache_clear()
        s = runtime.get_settings()
        self.assertEqual(s.business_crm_map.get('aiphoneguy'), 'ghl')
        self.assertEqual(s.business_crm_map.get('callingdigital'), 'attio')
        self.assertEqual(s.business_crm_map.get('autointelligence'), 'hubspot')

    def test_agent_override_wins(self):
        os.environ['BUSINESS_CRM_MAP'] = json.dumps({'callingdigital': 'attio'})
        os.environ['AGENT_CRM_MAP'] = json.dumps({'marcus': 'hubspot'})
        runtime.get_settings.cache_clear()
        s = runtime.get_settings()
        self.assertEqual(s.resolve_crm_provider('callingdigital', 'marcus'), 'hubspot')
        self.assertEqual(s.resolve_crm_provider('callingdigital', 'carlos'), 'attio')

    def test_provider_readiness(self):
        os.environ['HUBSPOT_API_KEY'] = 'x'
        os.environ['ATTIO_API_KEY'] = ''
        os.environ['GHL_API_KEY'] = 'x'
        os.environ['GHL_LOCATION_ID'] = 'x'
        runtime.get_settings.cache_clear()
        s = runtime.get_settings()
        self.assertTrue(s.crm_provider_ready('hubspot'))
        self.assertFalse(s.crm_provider_ready('attio'))
        self.assertTrue(s.crm_provider_ready('ghl'))


if __name__ == '__main__':
    unittest.main()
