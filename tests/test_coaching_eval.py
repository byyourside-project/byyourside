import copy
import json
import unittest
from pathlib import Path

from src.coaching_eval import evaluate_cases
from src.presentation import PhraseCoach


def dataset(status="explained", phase="ongoing", missing=False):
    return {"cases": [{"id": "one", "category": "test", "point": "기기 안에서 인식합니다",
                       "phase": phase, "utterances": [{"text": "기기 안에서 인식합니다"}],
                       "expected_status": status, "expected_missing": missing}]}


class EvaluationTests(unittest.TestCase):
    def test_reference_labels_are_not_sent_to_provider(self):
        jobs = []
        class InspectCoach(PhraseCoach):
            def evaluate(self, job):
                jobs.append(copy.deepcopy(job))
                return super().evaluate(job)
        report = evaluate_cases(dataset(), InspectCoach())
        self.assertEqual(report["status"], "DEVELOPMENT_PASS")
        self.assertNotIn("expected_status", json.dumps(jobs))
        self.assertNotIn("expected_missing", json.dumps(jobs))
        self.assertNotIn("category", json.dumps(jobs))
        self.assertEqual(report["generalization_status"], "NOT_RUN")

    def test_failure_cannot_count_as_correct_uncertainty(self):
        class TimeoutCoach:
            name = "timeout"
            def evaluate(self, job):
                raise TimeoutError("budget")
        report = evaluate_cases(dataset(status="uncertain"), TimeoutCoach())
        self.assertEqual(report["cases"][0]["actual_status"], "uncertain")
        self.assertEqual(report["correct_cases"], 0)
        self.assertEqual(report["error_cases"], 1)
        self.assertEqual(report["valid_response_rate"], 0)
        self.assertEqual(report["status"], "CHANGES_REQUIRED")

    def test_hallucinated_evidence_fails_structural_gate(self):
        class InvalidCoach(PhraseCoach):
            def evaluate(self, job):
                response = super().evaluate(job)
                response["judgments"][0]["evidence_segment_ids"] = ["invented"]
                return response
        report = evaluate_cases(dataset(), InvalidCoach())
        self.assertFalse(report["gate_breakdown"]["response_validity"])
        self.assertEqual(report["correct_cases"], 0)

    def test_wrong_explained_state_and_false_missing_are_separate_metrics(self):
        wrong = dataset(status="unconfirmed")
        report = evaluate_cases(wrong, PhraseCoach())
        self.assertEqual(report["false_explained"]["count"], 1)
        self.assertEqual(report["false_missing"]["count"], 0)
        wrong = dataset(phase="transition")
        wrong["cases"][0]["utterances"][0]["text"] = "다른 표현으로 같은 내용을 전달합니다"
        report = evaluate_cases(wrong, PhraseCoach())
        self.assertEqual(report["false_missing"]["count"], 1)
        self.assertEqual(report["false_explained"]["count"], 0)

    def test_only_deferred_cases_cannot_fake_a_model_pass(self):
        source = dataset(status="unconfirmed")
        source["cases"][0]["utterances"][0]["endpoint_reason"] = "hard_max_duration"
        report = evaluate_cases(source, PhraseCoach())
        self.assertEqual(report["accuracy"], 1)
        self.assertEqual(report["inference_calls"], 0)
        self.assertIsNone(report["valid_response_rate"])
        self.assertNotEqual(report["status"], "DEVELOPMENT_PASS")

    def test_invalid_reference_is_rejected_before_provider_call(self):
        class MustNotCall(PhraseCoach):
            def evaluate(self, job):
                raise AssertionError("provider should not run")
        source = dataset()
        source["cases"][0]["expected_status"] = "PASS"
        with self.assertRaises(ValueError):
            evaluate_cases(source, MustNotCall())

    def test_development_cases_cover_hard_cut_and_missing_policy(self):
        source = json.loads(Path("examples/coaching_eval.json").read_text())
        report = evaluate_cases(source, PhraseCoach())
        rows = {row["id"]: row for row in report["cases"]}
        self.assertEqual(report["total_cases"], 20)
        self.assertTrue(rows["C13"]["actual_missing"])
        self.assertFalse(rows["C12"]["actual_missing"])
        self.assertTrue(rows["C14"]["correct"])
        self.assertTrue(rows["C15"]["correct"])
        self.assertEqual(report["error_cases"], 0)


if __name__ == "__main__":
    unittest.main()
