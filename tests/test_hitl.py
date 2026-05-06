"""Unit tests for the HITL plane (no GCP, no LLM calls).

Covers the pure functions: disagreement selection, agreement-rate report,
and the dataframe → Annotation conversion. The BQ I/O layer
(`fetch_*`, `insert_annotations`) is intentionally not exercised here —
that's covered by the integration smoke run.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agent_eval.evaluation import hitl


def _trace_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestSelectDisagreements:
    def test_judge_passes_code_fails(self):
        df = _trace_df([
            {"run_id": "r1", "prompt": "p", "response": "r",
             "programmatic_pass": False, "safety_score": 0.95},
        ])
        out = hitl.select_disagreements(df)
        assert len(out) == 1
        assert out["disagreement_reason"].iloc[0] == "judge_pass_code_fail"

    def test_code_passes_judge_fails(self):
        df = _trace_df([
            {"run_id": "r1", "prompt": "p", "response": "r",
             "programmatic_pass": True, "safety_score": 0.4},
        ])
        out = hitl.select_disagreements(df)
        assert len(out) == 1
        assert out["disagreement_reason"].iloc[0] == "judge_fail_code_pass"

    def test_borderline_judge_score_queued(self):
        # Borderline fires when there's no explicit disagreement: both planes
        # say fail but the judge is just barely below threshold — human asked
        # to confirm the judge's call.
        df = _trace_df([
            {"run_id": "r1", "prompt": "p", "response": "r",
             "programmatic_pass": False, "safety_score": 0.78},
        ])
        out = hitl.select_disagreements(df)
        assert len(out) == 1
        assert out["disagreement_reason"].iloc[0] == "judge_borderline"

    def test_clear_pass_skipped(self):
        df = _trace_df([
            {"run_id": "r1", "prompt": "p", "response": "r",
             "programmatic_pass": True, "safety_score": 0.99},
        ])
        out = hitl.select_disagreements(df)
        assert out.empty

    def test_clear_fail_both_planes_skipped(self):
        # If both planes agree it's bad, no human needed for adjudication.
        df = _trace_df([
            {"run_id": "r1", "prompt": "p", "response": "r",
             "programmatic_pass": False, "safety_score": 0.4},
        ])
        out = hitl.select_disagreements(df)
        assert out.empty

    def test_explicit_disagreement_beats_borderline(self):
        # code_pass=True + safety=0.78 is BOTH borderline and an explicit
        # judge_fail_code_pass disagreement. The latter is more informative
        # so it wins.
        df = _trace_df([
            {"run_id": "r1", "prompt": "p", "response": "r",
             "programmatic_pass": True, "safety_score": 0.78},
        ])
        out = hitl.select_disagreements(df)
        assert len(out) == 1
        assert out["disagreement_reason"].iloc[0] == "judge_fail_code_pass"

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["safety_score", "programmatic_pass"])
        out = hitl.select_disagreements(df)
        assert out.empty
        assert "disagreement_reason" in out.columns

    def test_missing_programmatic_pass_only_borderline_picks_up(self):
        # Rows from before programmatic_pass was added — only borderline rule fires.
        df = _trace_df([
            {"run_id": "r1", "prompt": "p", "response": "r",
             "safety_score": 0.78},
            {"run_id": "r2", "prompt": "p", "response": "r",
             "safety_score": 0.99},
        ])
        out = hitl.select_disagreements(df)
        assert len(out) == 1
        assert out["disagreement_reason"].iloc[0] == "judge_borderline"


class TestAgreementRates:
    def test_perfect_agreement(self):
        df = pd.DataFrame([
            {"human_verdict": "good", "judge_safety": 0.95, "code_pass": True},
            {"human_verdict": "bad",  "judge_safety": 0.4,  "code_pass": False},
        ])
        out = hitl.agreement_rates(df)
        assert out["labeled"] == 2
        assert out["judge_agree_rate"] == 1.0
        assert out["code_agree_rate"] == 1.0

    def test_judge_disagrees_human_says_bad(self):
        df = pd.DataFrame([
            {"human_verdict": "bad", "judge_safety": 0.95, "code_pass": False},
        ])
        out = hitl.agreement_rates(df)
        # judge said pass (0.95 >= 0.9), human said bad → disagree
        assert out["judge_agree_rate"] == 0.0
        # code said fail, human said bad → agree
        assert out["code_agree_rate"] == 1.0

    def test_partial_counts_as_pass(self):
        df = pd.DataFrame([
            {"human_verdict": "partial", "judge_safety": 0.95, "code_pass": True},
        ])
        out = hitl.agreement_rates(df)
        assert out["judge_agree_rate"] == 1.0

    def test_skip_rows_excluded(self):
        df = pd.DataFrame([
            {"human_verdict": "skip", "judge_safety": 0.95, "code_pass": True},
        ])
        out = hitl.agreement_rates(df)
        assert out["labeled"] == 0

    def test_empty_input(self):
        out = hitl.agreement_rates(pd.DataFrame())
        assert out["labeled"] == 0
        assert out["judge_agree_rate"] == 0.0
        assert out["code_agree_rate"] == 0.0


class TestAnnotationBuilder:
    def test_builds_stable_id(self):
        a = hitl.make_annotation_id("r1", "p", "resp")
        b = hitl.make_annotation_id("r1", "p", "resp")
        assert a == b
        c = hitl.make_annotation_id("r2", "p", "resp")
        assert a != c

    def test_builds_annotation_from_disagreement_row(self):
        df = _trace_df([
            {"run_id": "r1", "prompt": "p", "response": "r",
             "programmatic_pass": False, "safety_score": 0.95,
             "experiment": "exp1", "category": "billing",
             "expected_route": "billing_agent",
             "disagreement_reason": "judge_pass_code_fail"},
        ])
        anns = hitl.build_annotations_from_disagreements(df)
        assert len(anns) == 1
        a = anns[0]
        assert a.status == "pending"
        assert a.code_pass is False
        assert a.judge_safety == pytest.approx(0.95)
        assert a.disagreement_reason == "judge_pass_code_fail"
        assert a.run_id == "r1"

    def test_to_bq_row_stamps_created_at(self):
        a = hitl.Annotation(
            annotation_id="abc", status="pending", run_id="r1",
            experiment=None, source=None, prompt="p", response="r",
            expected_route=None, category=None, code_pass=False, code_score=None,
            judge_safety=0.95, judge_custom=None,
            disagreement_reason="judge_pass_code_fail",
        )
        row = a.to_bq_row()
        assert row["created_at"]  # auto-stamped
        assert row["annotation_id"] == "abc"


class TestLabelLoop:
    def test_label_loop_writes_verdict_and_note(self, monkeypatch):
        # Stub fetch + insert; exercise the prompt logic with scripted input.
        pending = pd.DataFrame([{
            "annotation_id": "abc",
            "run_id": "r1",
            "experiment": "exp1",
            "source": "eval",
            "prompt": "Refund please",
            "response": "Sure, REF-INV-1",
            "expected_route": "billing_agent",
            "category": "billing",
            "code_pass": False,
            "code_score": 0.5,
            "judge_safety": 0.95,
            "judge_custom": 0.8,
            "disagreement_reason": "judge_pass_code_fail",
        }])
        monkeypatch.setattr(hitl, "fetch_pending", lambda *a, **k: pending)
        inserted: list = []
        monkeypatch.setattr(hitl, "insert_annotations",
                            lambda pid, anns: inserted.extend(anns) or len(anns))

        scripted = iter(["bad", "tool not actually run despite being mentioned"])
        n = hitl.cmd_label(
            "test-project",
            labeler="me@example.com",
            prompt_fn=lambda _msg: next(scripted),
            print_fn=lambda *_a, **_k: None,
        )

        assert n == 1
        assert len(inserted) == 1
        wrote = inserted[0]
        assert wrote.status == "labeled"
        assert wrote.human_verdict == "bad"
        assert wrote.human_note.startswith("tool not actually run")
        assert wrote.labeler == "me@example.com"
        assert wrote.labeled_at is not None

    def test_label_loop_quit_short_circuits(self, monkeypatch):
        pending = pd.DataFrame([
            {"annotation_id": "a", "run_id": "r1", "prompt": "p1", "response": "r1",
             "code_pass": False, "judge_safety": 0.95,
             "disagreement_reason": "judge_pass_code_fail"},
            {"annotation_id": "b", "run_id": "r2", "prompt": "p2", "response": "r2",
             "code_pass": True, "judge_safety": 0.4,
             "disagreement_reason": "judge_fail_code_pass"},
        ])
        monkeypatch.setattr(hitl, "fetch_pending", lambda *a, **k: pending)
        monkeypatch.setattr(hitl, "insert_annotations", lambda pid, anns: len(list(anns)))
        n = hitl.cmd_label(
            "test-project",
            labeler="me@example.com",
            prompt_fn=lambda _msg: "quit",
            print_fn=lambda *_a, **_k: None,
        )
        assert n == 0

    def test_label_loop_skip_skips(self, monkeypatch):
        pending = pd.DataFrame([{
            "annotation_id": "a", "run_id": "r1", "prompt": "p", "response": "r",
            "code_pass": False, "judge_safety": 0.95,
            "disagreement_reason": "judge_pass_code_fail",
        }])
        monkeypatch.setattr(hitl, "fetch_pending", lambda *a, **k: pending)
        inserted: list = []
        monkeypatch.setattr(hitl, "insert_annotations",
                            lambda pid, anns: inserted.extend(anns) or len(anns))
        n = hitl.cmd_label(
            "test-project",
            labeler="me@example.com",
            prompt_fn=lambda _msg: "skip",
            print_fn=lambda *_a, **_k: None,
        )
        assert n == 0
        assert inserted == []
