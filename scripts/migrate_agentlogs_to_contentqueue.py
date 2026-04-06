import os
import sys
import psycopg2
from psycopg2.extras import DictCursor

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL not set")
    sys.exit(1)

AGENT_NAME = "aiphoneguy"

with psycopg2.connect(DATABASE_URL) as conn:
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute("""
            SELECT content, created_at FROM agent_logs
            WHERE agent_name = %s
            ORDER BY created_at DESC
        """, (AGENT_NAME,))
        rows = cur.fetchall()
        print(f"Found {len(rows)} logs for {AGENT_NAME}")
        queued = 0
        for row in rows:
            content = row[0]
            # Simple heuristic: treat each log as a Facebook post
            cur.execute("""
                INSERT INTO content_queue (business_key, agent_name, platform, content_type, title, body, hashtags, cta, funnel_stage, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s)
            """, (
                AGENT_NAME,
                "zoe",
                "facebook",
                "post",
                "AI Phone Guy Dashboard Migration",
                content[:100],  # Use first 100 chars as body
                "#AI #PhoneGuy #Migration",
                "Call now for your AI phone demo!",
                "awareness",
                row[1],
            ))
            queued += 1
        conn.commit()
        print(f"Queued {queued} posts from agent_logs to content_queue.")
