import os
import json
import unittest
from unittest.mock import patch, MagicMock

from config import runtime
from services.errors import DatabaseError


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


class CrmConfigWriteEndpointTests(unittest.TestCase):
    """Tests for POST /api/crm/config — runtime CRM reconfiguration."""

    def setUp(self):
        self._backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._backup)
        runtime.get_settings.cache_clear()

    def test_post_crm_config_updates_env_and_clears_cache(self):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        # Ensure no auth gate
        os.environ.pop('API_KEYS', None)
        runtime.get_settings.cache_clear()

        payload = {"business_crm_map": {"aiphoneguy": "hubspot"}}
        resp = client.post("/api/crm/config", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("business_crm_map", data["updated"])
        self.assertEqual(os.environ.get("BUSINESS_CRM_MAP"), '{"aiphoneguy": "hubspot"}')

    def test_post_crm_config_rejects_invalid_provider(self):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        os.environ.pop('API_KEYS', None)
        runtime.get_settings.cache_clear()

        payload = {"business_crm_map": {"aiphoneguy": "salesforce"}}
        resp = client.post("/api/crm/config", json=payload)
        self.assertEqual(resp.status_code, 422)

    def test_post_crm_config_rejects_empty_payload(self):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        os.environ.pop('API_KEYS', None)
        runtime.get_settings.cache_clear()

        resp = client.post("/api/crm/config", json={})
        self.assertEqual(resp.status_code, 400)


class DatabaseServiceLayerTests(unittest.TestCase):
    """Tests for services/database.py retry helpers."""

    def test_execute_query_raises_database_error_without_url(self):
        from services.database import execute_query
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            with self.assertRaises(DatabaseError) as ctx:
                execute_query("SELECT 1")
            self.assertIn("DATABASE_URL", str(ctx.exception))

    def test_fetch_all_raises_database_error_without_url(self):
        from services.database import fetch_all
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            with self.assertRaises(DatabaseError) as ctx:
                fetch_all("SELECT 1")
            self.assertIn("DATABASE_URL", str(ctx.exception))

    def test_database_error_is_typed(self):
        err = DatabaseError("test_op", "something broke", retryable=True)
        self.assertEqual(err.operation, "test_op")
        self.assertTrue(err.retryable)
        self.assertIn("test_op", str(err))


if __name__ == '__main__':
    unittest.main()

