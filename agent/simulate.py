"""Text simulator — run a scripted (or interactive) call against an agent.

No accounts, no keys, no audio. The same dialogue engine and tools the
LiveKit voice worker uses, driven by typed input instead of speech.

Usage:
    python -m agent.simulate --config configs/clinic.yaml --script sample_data/call1_book.txt
    python -m agent.simulate --config configs/clinic.yaml            # interactive
    python -m agent.simulate --config configs/clinic.yaml --script sample_data/call1_book.txt \\
        --store /tmp/calls.db --out sample_output/transcript-call1.json --today 2027-01-10
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile

from .config import ConfigError, load_config
from .dialogue import DialogueEngine
from .store import BookingStore
from .tools import AgentTools

__all__ = ["run_simulation"]


def run_simulation(*, config_path: str, script: list[str], store_path: str,
                   today: _dt.date) -> dict:
    """Run caller lines through the engine. Returns the full transcript dict."""
    config = load_config(config_path)
    store = BookingStore(store_path)
    try:
        tools = AgentTools(config, store)
        engine = DialogueEngine(config, tools, today=today)
        turns: list[dict] = []
        greeting = engine.greeting()
        turns.append({"speaker": "agent", "text": greeting, "tool_calls": []})
        for line in script:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            reply, calls = engine.process_turn(line)
            turns.append({"speaker": "caller", "text": line, "tool_calls": []})
            turns.append({"speaker": "agent", "text": reply, "tool_calls": calls})
        bookings = store.list_for()
        return {
            "business": config["business"]["name"],
            "config": os.path.basename(config_path),
            "today": today.isoformat(),
            "turns": turns,
            "bookings": bookings,
        }
    finally:
        store.close()


def _print_transcript(transcript: dict) -> None:
    for turn in transcript["turns"]:
        who = "Agent" if turn["speaker"] == "agent" else "Caller"
        print(f"{who}: {turn['text']}")
        for call in turn["tool_calls"]:
            print(f"  [tool: {call['tool']} -> ok={call['result'].get('ok')}]")
    print(f"\nBookings on file: {len(transcript['bookings'])}")
    for b in transcript["bookings"]:
        print(f"  {b['code']}: {b['service']} with {b['resource']} on {b['date']} "
              f"{b['start']}–{b['end']} ({b['customer_name']}, {b['status']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent.simulate",
        description="Text simulator for the call agent — no accounts, no keys, no audio.")
    parser.add_argument("--config", required=True, help="client YAML config")
    parser.add_argument("--script", help="file with one caller line per turn (omit for interactive)")
    parser.add_argument("--store", help="SQLite path (default: throwaway temp file)")
    parser.add_argument("--out", help="write the full transcript JSON here")
    parser.add_argument("--today", default=_dt.date.today().isoformat(),
                        help="reference date for 'today'/'tomorrow' (default: real today)")
    args = parser.parse_args(argv)

    try:
        today = _dt.date.fromisoformat(args.today)
    except ValueError:
        print(f"error: --today must be YYYY-MM-DD, got {args.today!r}", file=sys.stderr)
        return 2

    if args.script:
        if not os.path.exists(args.script):
            print(f"error: script file not found: {args.script}", file=sys.stderr)
            return 1
        with open(args.script, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    else:
        print("Interactive mode — type caller lines, Ctrl-D / 'quit' to end.\n")
        lines = []
        try:
            while True:
                line = input("Caller: ").strip()
                if line.lower() in ("quit", "exit"):
                    break
                lines.append(line)
        except EOFError:
            pass

    store_path = args.store or os.path.join(tempfile.mkdtemp(prefix="call-agent-"), "calls.db")
    try:
        transcript = run_simulation(config_path=args.config, script=lines,
                                    store_path=store_path, today=today)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print_transcript(transcript)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(transcript, fh, indent=2, ensure_ascii=False)
        print(f"\nTranscript written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
