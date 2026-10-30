"""Inspect call logs. Examples:
    python view_logs.py
    python view_logs.py --outcome Escalated --limit 20
    python view_logs.py --export logs.csv
"""
import argparse
import os
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).resolve().parent / "call_logs.db"))

parser = argparse.ArgumentParser(description="View AI calling assistant logs")
parser.add_argument("--limit", type=int, default=50)
parser.add_argument("--outcome", choices=["Resolved", "Escalated"])
parser.add_argument("--export", metavar="FILE.csv", help="Save the result as a CSV file")
args = parser.parse_args()

query = """
SELECT 
    m.id,
    c.session_id,
    m.timestamp,
    m.detected_intent,
    m.outcome,
    m.user_transcript,
    m.ai_response
FROM call_messages m
JOIN calls c ON m.call_id = c.id
"""
params = []
if args.outcome:
    query += " WHERE m.outcome = ?"
    params.append(args.outcome)
query += " ORDER BY m.id DESC LIMIT ?"
params.append(args.limit)

if not DB_PATH.exists():
    print(f"Database not found at {DB_PATH}")
    exit(0)

with sqlite3.connect(DB_PATH) as conn:
    df = pd.read_sql_query(query, conn, params=params)

if df.empty:
    print("No logs found.")
else:
    pd.set_option("display.max_colwidth", 60, "display.width", 200)
    print(df.to_string(index=False))
    resolved_count = (df.outcome == "Resolved").sum() if "outcome" in df.columns else 0
    escalated_count = (df.outcome == "Escalated").sum() if "outcome" in df.columns else 0
    print(f"\nRows: {len(df)} | Resolved: {resolved_count} | Escalated: {escalated_count}")
    if args.export:
        df.to_csv(args.export, index=False, encoding="utf-8-sig")
        print(f"Exported to {args.export}")
