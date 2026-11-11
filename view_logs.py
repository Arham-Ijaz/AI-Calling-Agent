"""Inspect call logs from MongoDB Atlas. Examples:
    python view_logs.py
    python view_logs.py --outcome Escalated --limit 20
    python view_logs.py --export logs.csv
"""
import argparse
import csv
import os
from dotenv import load_dotenv
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
resolved_count = 0
escalated_count = 0
for m in messages:
    call = db["calls"].find_one({"id": m.get("call_id")})
    outcome = m.get("outcome", "Resolved")
    if outcome == "Resolved":
        resolved_count += 1
    elif outcome == "Escalated":
        escalated_count += 1
    records.append({
        "id": m.get("id"),
        "session_id": call.get("session_id") if call else "unknown",
        "timestamp": m.get("timestamp"),
        "detected_intent": m.get("detected_intent"),
        "outcome": outcome,
        "user_transcript": m.get("user_transcript"),
        "ai_response": m.get("ai_response"),
    })

# Format and display table cleanly using built-in string formatting
header = f"{'ID':<6} | {'SESSION':<22} | {'INTENT':<18} | {'OUTCOME':<10} | {'TRANSCRIPT':<32} | {'REPLY'}"
print(header)
print("-" * len(header))
for r in records:
    sid = str(r["session_id"] or "")[:20]
    intent = str(r["detected_intent"] or "")[:16]
    out = str(r["outcome"] or "")[:9]
    trans = str(r["user_transcript"] or "").replace("\n", " ")[:30]
    rep = str(r["ai_response"] or "").replace("\n", " ")[:45]
    print(f"{r['id']:<6} | {sid:<22} | {intent:<18} | {out:<10} | {trans:<32} | {rep}")

print(f"\nRows: {len(records)} | Resolved: {resolved_count} | Escalated: {escalated_count}")

if args.export:
    with open(args.export, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "session_id", "timestamp", "detected_intent", "outcome", "user_transcript", "ai_response"])
        writer.writeheader()
        writer.writerows(records)
    print(f"Exported to {args.export}")
