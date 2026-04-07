
import os
from services.database import fetch_all
from dotenv import load_dotenv

load_dotenv()

def check():
    print("Checking agent_logs...")
    try:
        rows = fetch_all("SELECT agent_name, COUNT(*), MAX(created_at) FROM agent_logs GROUP BY agent_name;")
        for row in rows:
            print(f"Agent: {row[0]}, Count: {row[1]}, Last Run: {row[2]}")
    except Exception as e:
        print(f"Error querying agent_logs: {e}")

    print("\nChecking revenue_events...")
    try:
        rows = fetch_all("SELECT event_type, COUNT(*), MAX(created_at) FROM revenue_events GROUP BY event_type;")
        for row in rows:
            print(f"Event: {row[0]}, Count: {row[1]}, Last Run: {row[2]}")
    except Exception as e:
        print(f"Error querying revenue_events: {e}")

    print("\nChecking artifacts...")
    try:
        rows = fetch_all("SELECT status, COUNT(*) FROM artifacts GROUP BY status;")
        for row in rows:
            print(f"Status: {row[0]}, Count: {row[1]}")
    except Exception as e:
        print(f"Error querying artifacts: {e}")

if __name__ == "__main__":
    check()
