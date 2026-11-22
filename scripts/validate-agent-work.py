"""Validate the lightweight Personal Hub contract (JSON-compatible YAML, stdlib only).

Usage: python scripts/validate-agent-work.py work/PH-ACTIVITY-TIMELOG-001
This checks project semantics; it does not pretend to execute acceptance tests.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path


PLAN_TO_STATE = {"pending": "pending", "in_progress": "in_progress", "passed": "completed",
                 "failed": "blocked", "blocked": "blocked"}
RESULTS = {"passed", "failed", "blocked", "not_run", "not_applicable"}


def validate(folder):
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    try:
        task, plan, state = [json.loads((folder / name).read_text(encoding="utf-8"))
                             for name in ("task.yaml", "plan.yaml", "state.json")]
        check(all(d["schema_version"] == "1.0" for d in (task, plan, state)), "schema_version")
        check(task["id"] == plan["task_id"] == state["task_id"], "task identity")
        check(task["scope"]["in"] and task["scope"]["out"] and task["invariants"], "scope")
        check(task["autonomy"] in {"balanced", "supervised", "autonomous"}, "autonomy")
        check(task["risk"] in {"low", "medium", "high"}, "risk")
        check(task["autonomy"] != "autonomous" or task["risk"] == "low", "autonomy/risk")
        check(datetime.fromisoformat(state["updated_at"]).utcoffset() is not None, "timestamp")
        delivery = state["delivery_plan"]
        check(delivery["path"] == "plan.yaml" and
              delivery["plan_revision"] == plan["plan_revision"], "plan revision")
        check([s["id"] for s in plan["stages"]] == [s["id"] for s in delivery["stages"]],
              "stage order")
        criteria = {c["id"]: c for c in state["criterion_results"]}
        expected = [c["id"] for c in task["acceptance_criteria"]]
        linked = [c for s in plan["stages"] for c in s["acceptance_criteria"]]
        check(len(criteria) == len(state["criterion_results"]) and
              len(set(expected)) == len(expected) and set(criteria) == set(expected) and
              sorted(linked) == sorted(expected), "criteria coverage")
        for c in criteria.values():
            check(c["result"] in RESULTS, "criterion result")
            check(bool(c["evidence"]) if c["result"] == "passed" else bool(c["notes"]),
                  "criterion evidence or explanation")
        previous, statuses = set(), {s["id"]: s["status"] for s in delivery["stages"]}
        for p, s in zip(plan["stages"], delivery["stages"]):
            check(p["id"] not in previous and set(p["depends_on"]) <= previous, "dependencies")
            check(p["value"] and p["deliverables"] and p["acceptance_criteria"], "stage value")
            check(p["status"] in PLAN_TO_STATE and
                  PLAN_TO_STATE.get(p["status"]) == s["status"], "stage status mapping")
            check(s["status"] == "completed" or s["completed_at"] is None, "completion date")
            if s["status"] in {"in_progress", "completed"}:
                check(bool(s["started_at"]), "start date")
            if s["status"] == "completed":
                check(s["completed_at"] and s["evidence"], "completed stage evidence")
                check(all(criteria[c]["result"] in {"passed", "not_applicable"}
                          for c in p["acceptance_criteria"]), "stage acceptance")
                check(all(statuses[d] == "completed" for d in p["depends_on"]), "stage dependency")
            if s["status"] == "blocked":
                check(bool(s["notes"]), "blocked stage reason")
            previous.add(p["id"])
        check(delivery["active_stage_id"] is None or
              delivery["active_stage_id"] in previous, "active stage")
        check(state["stage"] in {"intake", "discovery", "planning", "implementation",
                                  "validation", "handoff", "closed"}, "phase")
        check(state["status"] in {"queued", "in_progress", "awaiting_approval", "blocked",
                                   "done", "cancelled"}, "overall status")
        check(state["user_acceptance"] in {"unreviewed", "accepted", "rejected"}, "acceptance")
        check(bool(state["next_action"]), "next action")
        check((state["status"] == "blocked") == bool(state["blockers"]), "blockers")
        check((state["status"] == "awaiting_approval") == bool(state["pending_approval"]),
              "approval")
        tests = state["tests"]
        check(len({t["id"] for t in tests}) == len(tests), "test identity")
        for t in tests:
            check(t["result"] in RESULTS and t["procedure"] and t["environment"], "test result")
            check(t["result"] not in {"passed", "failed"} or t["evidence"], "test evidence")
        if state["status"] == "done":
            check(state["stage"] == "closed" and state["user_acceptance"] != "rejected",
                  "technical closure")
            check(all(s["status"] == "completed" for s in delivery["stages"]), "unfinished stages")
            check(all(c["result"] in {"passed", "not_applicable"} for c in criteria.values()) and
                  all(t["result"] in {"passed", "not_applicable"} for t in tests), "unfinished checks")
        check(state["stage"] != "closed" or state["status"] in {"done", "cancelled"}, "closure")
        report = Path(state["report_path"])
        check(not report.is_absolute() and ".." not in report.parts and
              (folder / report).is_file(), "report")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        errors.append("Invalid files, structure or timestamp; use JSON-compatible YAML")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for folder in args.folders:
        errors = validate(folder)
        failed |= bool(errors)
        print(f"{'FAIL' if errors else 'PASS'} {folder}")
        for error in dict.fromkeys(errors):
            print(f"  - {error}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
