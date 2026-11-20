#!/usr/bin/env bash
# Run all four scripted demo calls and regenerate sample_output/.
# Usage: bash scripts/run_simulations.sh
# Needs only the core dependencies (pip install -r requirements.txt).
set -euo pipefail
cd "$(dirname "$0")/.."

TODAY="2027-01-10"
OUT="sample_output"
mkdir -p "$OUT"
rm -f "$OUT"/transcript-*.json "$OUT"/booking-record.json

run_call() {
  local num="$1" config="$2" script="$3"
  local store="$OUT/.calls-$num.db"
  rm -f "$store"
  echo "=== call $num ($config) ==="
  python3 -m agent.simulate --config "$config" --script "sample_data/$script" \
    --today "$TODAY" --store "$store" --out "$OUT/transcript-$script.json"
  rm -f "$store"
  echo
}

run_call 1 configs/clinic.yaml call1_book.txt
run_call 2 configs/clinic.yaml call2_hours.txt
run_call 3 configs/clinic.yaml call3_cancel.txt
run_call 4 configs/real-estate.yaml call4_realestate.txt

# The booking record for the demo page: the real booking from call 1.
python3 - "$OUT/transcript-call1_book.txt.json" <<'EOF'
import json, sys
t = json.load(open(sys.argv[1]))
b = t["bookings"][0]
record = {
    "booking": b,
    "source": "sample_data/call1_book.txt via python -m agent.simulate",
    "verified": "double-booking the same slot is rejected; see tests/test_store.py",
}
with open("sample_output/booking-record.json", "w", encoding="utf-8") as fh:
    json.dump(record, fh, indent=2)
print("booking-record.json written:", b["code"])
EOF

echo "Done — see $OUT/ for transcripts, booking-record.json and index.html."
