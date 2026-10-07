"""Checks the Conductor's routing against the sample sentences, using the
real LLM. Run on the Pi with Ollama running:

    python -m tests.eval_conductor              keyword shortcuts + LLM (as in use)
    python -m tests.eval_conductor --llm-only   LLM alone, to judge the model
"""

import argparse
import time

import config
from agents.conductor import Conductor
from llm import LLM
from tests.conductor_cases import CASES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-only", action="store_true", help="skip the keyword shortcuts")
    parser.add_argument("--model", default=config.LLM_MODEL)
    args = parser.parse_args()

    llm = LLM(model=args.model)
    if not llm.load(attempts=3):
        raise SystemExit("Ollama is not responding.")
    agents = dict.fromkeys(("schedule", "search", "remember", "answer"), object())
    conductor = Conductor(llm, db=None, agents=agents, use_shortcuts=not args.llm_only)

    wrong, llm_calls, llm_time = 0, 0, 0.0
    for text, expected in CASES:
        started = time.monotonic()
        label, how = conductor.classify(text)
        if how != "shortcut":
            llm_calls += 1
            llm_time += time.monotonic() - started
        ok = label == expected
        wrong += not ok
        print(f"{'ok ' if ok else 'BAD'} {label:9} {how:9} {text}" + ("" if ok else f"  (expected {expected})"))

    total = len(CASES)
    print(f"\n{args.model}: {total - wrong}/{total} correct ({100 * (total - wrong) / total:.0f}%)")
    if llm_calls:
        print(f"{llm_calls} LLM calls, {llm_time / llm_calls:.1f}s each on average")


if __name__ == "__main__":
    main()
