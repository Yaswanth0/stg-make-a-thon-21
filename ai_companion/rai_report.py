"""Responsible-AI report: what Rabbit stores, and what its guardrails and
privacy controls did. Run on the Pi:

    python rai_report.py            last 30 days
    python rai_report.py --days 7
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta

import config
from db import Database


def main():
    parser = argparse.ArgumentParser(description="Rabbit's responsible-AI report")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    db = Database(config.DB_FILE)
    since = datetime.now() - timedelta(days=args.days)
    events = db.rai_events(since=since)

    print("Data stored on this Pi")
    for name, count in db.data_inventory().items():
        print(f"  {name:<10} {count}")
    print(f"  retention  conversations kept {config.HISTORY_RETENTION_DAYS or 'forever'} days\n")

    print(f"Events in the last {args.days} days")
    if not events:
        print("  none")
    for (kind, detail), count in Counter((e["kind"], e["detail"]) for e in events).most_common():
        print(f"  {count:>4}  {kind:<14} {detail}")

    if events:
        print("\nMost recent")
        for e in events[:10]:
            print(f"  {e['created_at']}  {e['kind']:<14} {e['detail']}")
    db.close()


if __name__ == "__main__":
    main()
