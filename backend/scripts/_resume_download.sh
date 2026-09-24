#!/bin/sh
# Resume FLK download: retry previously failed editions + continue pending ones
cd /app
for round in 1 2 3 4; do
  echo "===== ROUND $round ====="
  python scripts/flk_full_import.py --phase download --workers 3 --retry-failed 2>&1 | grep -E "download:|FAIL|SUMMARY|downloaded" | tail -5
  sleep 5
done
echo "ALL ROUNDS DONE"
