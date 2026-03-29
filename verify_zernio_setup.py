#!/usr/bin/env python3
"""
Zernio integration verification script.
Run this to validate Zernio is properly configured and discoverable.

Usage:
    python3 verify_zernio_setup.py
"""

import os
import sys
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s"
)

def verify_setup():
    """Verify Zernio integration is working."""
    print("=" * 80)
    print("ZERNIO INTEGRATION VERIFICATION")
    print("=" * 80)
    
    # 1. Check environment variable
    print("\n1. Checking ZERNIO_API_KEY...")
    api_key = os.getenv("ZERNIO_API_KEY", "").strip()
    if api_key:
        masked_key = api_key[:10] + "..." + api_key[-4:] if len(api_key) > 14 else "***"
        print(f"   ✓ ZERNIO_API_KEY is set: {masked_key}")
    else:
        print("   ✗ ZERNIO_API_KEY is NOT set")
        print("     Set it in .env or Railway environment variables")
    
    # 2. Import module
    print("\n2. Importing tools.zernio...")
    try:
        from tools.zernio import (
            zernio_ready,
            get_zernio_profiles,
            list_zernio_accounts,
        )
        print("   ✓ tools.zernio imported successfully")
    except Exception as e:
        print(f"   ✗ Failed to import: {e}")
        return False
    
    # 3. Check readiness
    print("\n3. Checking Zernio readiness...")
    if zernio_ready():
        print("   ✓ Zernio is configured and ready")
    else:
        print("   ✗ Zernio is NOT ready (missing API key)")
        return True  # Not a failure, just not configured
    
    # 4. Try to fetch profiles
    print("\n4. Attempting to fetch Zernio profiles...")
    try:
        profiles = get_zernio_profiles()
        print(f"   ✓ Found {len(profiles)} profile(s)")
        for p in profiles:
            name = p.get("name", "Unknown")
            profile_id = p.get("_id", "Unknown")
            print(f"      - {name} (ID: {profile_id})")
            
            # Get accounts for this profile
            try:
                accounts = list_zernio_accounts(profile_id)
                print(f"        └─ {len(accounts)} account(s)")
                for acc in accounts:
                    platform = acc.get("platform", "unknown")
                    username = acc.get("username", "N/A")
                    print(f"           - {platform}: @{username}")
            except Exception as e:
                print(f"        └─ Error fetching accounts: {e}")
    except Exception as e:
        print(f"   ✗ Failed to fetch profiles: {e}")
        print("   Make sure your ZERNIO_API_KEY is valid")
        return False
    
    # 5. Check app integration
    print("\n5. Checking app.py integration...")
    try:
        from app import app
        
        # Check if Zernio endpoint is registered
        routes = [r.path for r in app.routes]
        zernio_route = "/content/publish/zernio/{business_key}"
        if zernio_route in routes:
            print(f"   ✓ Found Zernio publishing endpoint: {zernio_route}")
        else:
            print(f"   ✗ Zernio endpoint NOT found in app routes")
            print(f"   Available routes: {[r for r in routes if 'publish' in r]}")
    except Exception as e:
        print(f"   ✗ Error checking app routes: {e}")
    
    print("\n" + "=" * 80)
    print("VERIFICATION COMPLETE")
    print("=" * 80)
    print("\nNext steps:")
    print("1. If Zernio is not configured:")
    print("   - Get API key at https://zernio.com/ (Settings → API Keys)")
    print("   - Set ZERNIO_API_KEY in .env or Railway")
    print("\n2. Create Zernio profiles and connect accounts:")
    print("   - Log into https://zernio.com/")
    print("   - Create profiles for each business (The AI Phone Guy, Calling Digital, etc.)")
    print("   - Connect social accounts (Twitter, LinkedIn, Instagram, etc.)")
    print("\n3. Test publishing:")
    print("   curl -X POST http://localhost:8000/content/publish/zernio/aiphoneguy?limit=5 \\")
    print("     -H 'Authorization: Bearer YOUR_API_KEY'")
    
    return True


if __name__ == "__main__":
    success = verify_setup()
    sys.exit(0 if success else 1)
