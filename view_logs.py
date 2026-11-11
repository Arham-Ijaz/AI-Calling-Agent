"""Inspect call logs from MongoDB Atlas. Examples:
    python view_logs.py
    python view_logs.py --outcome Escalated --limit 20
    python view_logs.py --export logs.csv
"""
import argparse
import os
from dotenv import load_dotenv
import pandas as pd
from pymongo import MongoClient

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    print("Error: MONGODB_URI environment variable is not set. Please configure .env")
    exit(1)
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ai_calling_db")

parser = argparse.ArgumentParser(description="View AI calling assistant logs from MongoDB Atlas")
parser.add_argument("--limit", type=int, default=50)
parser.add_argument("--outcome", choices=["Resolved", "Escalated"])
parser.add_argument("--export", metavar="FILE.csv", help="Save the result as a CSV file")
args = parser.parse_args()

client = MongoClient(MONGODB_URI)
db = client[MONGODB_DB_NAME]

filter_q = {}
if args.outcome:
    filter_q["outcome"] = args.outcome

messages = list(db["call_messages"].find(filter_q).sort("id", -1).limit(args.limit))

if not messages:
    print("No logs found.")
    exit(0)

# Join session_id from calls collection
records = []
for m in messages:
    call = db["calls"].find_one({"id": m.get("call_id")})
    records.append({
        "id": m.get("id"),
        "session_id": call.get("session_id") if call else "unknown",
        "timestamp": m.get("timestamp"),
        "detected_intent": m.get("detected_intent"),
        "outcome": m.get("outcome"),
        "user_transcript": m.get("user_transcript"),
        "ai_response": m.get("ai_response"),
    })

df = pd.DataFrame(records)

pd.set_option("display.max_colwidth", 60, "display.width", 200)
print(df.to_string(index=False))
resolved_count = (df.outcome == "Resolved").sum() if "outcome" in df.columns else 0
escalated_count = (df.outcome == "Escalated").sum() if "outcome" in df.columns else 0
print(f"\nRows: {len(df)} | Resolved: {resolved_count} | Escalated: {escalated_count}")
if args.export:
    df.to_csv(args.export, index=False, encoding="utf-8-sig")
    print(f"Exported to {args.export}")
