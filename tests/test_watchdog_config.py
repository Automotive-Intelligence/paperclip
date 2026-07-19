from config.watchdog_config import load_watchdog_config


def test_config_has_seven_sites_and_brands():
    cfg = load_watchdog_config()
    assert len(cfg["site_urls"]) == 7
    assert "https://theaiphoneguy.com" in cfg["site_urls"]
    assert cfg["brands"]["automotive_intelligence"]["blog_max_age_hours"] == 96
    # held/gap brands disabled via 0
    assert cfg["brands"]["worship_digital"]["blog_max_age_hours"] == 0
    assert cfg["emails_sent"]["min_prospects_for_alert"] == 25
