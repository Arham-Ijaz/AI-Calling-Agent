"""Inspect call logs.  Examples:
    python view_logs.py
    python view_logs.py --outcome Escalated --limit 20
    python view_logs.py --export logs.csv
"""
import argparse
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent / "call_logs.db"

parser = argparse.ArgumentParser(description="View AI calling assistant logs")
parser.add_argument("--limit", type=int, default=50)
parser.add_argument("--outcome", choices=["Resolved", "Escalated"])
parser.add_argument("--export", metavar="FILE.csv", help="Save the result as a CSV file")
args = parser.parse_args()

query, params = "SELECT * FROM session_logs", []
if args.outcome:
    query += " WHERE outcome = ?"
    params.append(args.outcome)
query += " ORDER BY id DESC LIMIT ?"
params.append(args.limit)

with sqlite3.connect(DB_PATH) as conn:
    df = pd.read_sql_query(query, conn, params=params)

if df.empty:
    print("No logs found.")
else:
    pd.set_option("display.max_colwidth", 60, "display.width", 200)
    print(df.to_string(index=False))
    print(f"\nRows: {len(df)} | Resolved: {(df.outcome == 'Resolved').sum()} | Escalated: {(df.outcome == 'Escalated').sum()}")
    if args.export:
        df.to_csv(args.export, index=False, encoding="utf-8-sig")
        print(f"Exported to {args.export}")
