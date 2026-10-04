import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.presentation import Session, PhraseCoach, LocalHttpCoach, validate_deck


DECK = {"deck_id": "test", "title": "발표", "total_duration_sec": 100,
        "slides": [{"slide_id": "s1", "title": "처음", "target_duration_sec": 20,
                    "keypoints": [{"keypoint_id": "p1", "text": "기기에서 음성을 인식합니다", "aliases": ["음성을 외부로 보내지 않고 인식합니다"], "required": True}]},
                   {"slide_id": "s2", "title": "다음", "target_duration_sec": 30,
                    "keypoints": [{"keypoint_id": "p2", "text": "발화를 최대 4초 구간으로 분할합니다", "required": True}]}]}


class FakeClock:
    def __init__(self):
        self.value = 1000.0
    def __call__(self):
        return self.value
    def advance(self, seconds):
        self.value += seconds


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.session = Session(DECK, clock=self.clock)
        self.coach = PhraseCoach()

    def feed(self, text, start=1, end=3, sid="one", endpoint="silence", status="OK"):
        self.clock.value = max(self.clock.value, self.session.origin + end + .1)
        return self.session.ingest({"segment_id": sid, "text": text, "start_sec": start,
                                    "end_sec": end, "endpoint_reason": endpoint, "status": status})

    def test_deck_rejects_duplicate_ids_and_nonfinite_duration(self):
        for invalid in (float("nan"), -1, True, 0):
            deck = copy.deepcopy(DECK)
            deck["total_duration_sec"] = invalid
            with self.assertRaises(ValueError):
                validate_deck(deck)
        deck = copy.deepcopy(DECK)
        deck["slides"][1]["keypoints"][0]["keypoint_id"] = "p1"
        with self.assertRaises(ValueError):
            validate_deck(deck)

    def test_timer_reset_and_total_clock_preserved(self):
        self.clock.advance(12)
        self.session.navigate(1)
        self.clock.advance(3)
        snap = self.session.snapshot()
        self.assertEqual(snap["elapsed_sec"], 15)
        self.assertEqual(snap["slide_elapsed_sec"], 3)
        self.assertEqual(self.session.visits[0]["end_sec"], 12)

    def test_time_alerts_are_deduplicated_and_priority_preempts(self):
        self.clock.advance(20)
        self.session.tick()
        self.assertEqual(self.session.alerts[-1]["key"], "slide_time:1")
        self.clock.advance(81)
        for _ in range(100):
            self.session.tick()
        self.assertEqual(sum(a["key"] == "total_over" for a in self.session.alerts), 1)
        self.assertEqual(self.session.snapshot()["alerts"][0]["priority"], 3)
        self.assertLess(len(self.session.events), 10)

    def test_alias_marks_explained_with_real_evidence(self):
        job = self.feed("음성을 외부로 보내지 않고 인식합니다")
        self.session.apply(job, self.coach.evaluate(job))
        self.assertEqual(self.session.states["p1"]["status"], "explained")
        self.assertEqual(self.session.states["p1"]["evidence_segment_ids"], ["one"])
        self.assertFalse(self.session.alerts)

    def test_negation_and_numeric_mismatch_are_uncertain(self):
        job = self.feed("기기에서 음성을 인식합니다 라는 말은 사실이 아닙니다")
        self.session.apply(job, self.coach.evaluate(job))
        self.assertEqual(self.session.states["p1"]["status"], "uncertain")
        self.clock.advance(1)
        self.session.navigate(1)
        start = self.session.elapsed() + .1
        job = self.feed("발화를 최대 5초 구간으로 분할합니다", start, start + 2, "two")
        self.session.apply(job, self.coach.evaluate(job))
        self.assertEqual(self.session.states["p2"]["status"], "uncertain")

    def test_hard_cut_waits_for_continuation(self):
        job = self.feed("기기에서 음성을", endpoint="hard_max_duration")
        self.assertIsNone(job)
        self.assertEqual(self.session.states["p1"]["status"], "unconfirmed")
        job = self.feed("인식합니다", start=3, end=4, sid="two")
        self.session.apply(job, self.coach.evaluate(job))
        self.assertEqual(self.session.states["p1"]["status"], "explained")
        self.assertEqual(self.session.states["p1"]["evidence_segment_ids"], ["one", "two"])

    def test_boundary_segment_is_uncertain_for_both_slides(self):
        self.clock.advance(2)
        self.session.navigate(1)
        self.assertIsNone(self.feed("기기에서 음성을 인식합니다", start=1, end=3))
        self.assertEqual(self.session.segments[-1]["slide_ids"], ["s1", "s2"])
        self.assertEqual(self.session.states["p1"]["status"], "uncertain")
        self.assertEqual(self.session.states["p2"]["status"], "uncertain")

    def test_late_result_updates_original_slide_without_current_alert(self):
        self.clock.advance(5)
        self.session.navigate(1)
        job = self.feed("기기에서 음성을 인식합니다")
        before = len(self.session.alerts)
        self.session.apply(job, self.coach.evaluate(job))
        self.assertEqual(self.session.states["p1"]["status"], "explained")
        self.assertEqual(self.session.states["p2"]["status"], "unconfirmed")
        self.assertEqual(len(self.session.alerts), before)

    def test_no_missing_alert_during_speech_and_boundary_reports_candidate(self):
        self.clock.advance(5)
        self.session.tick()
        self.assertFalse(self.session.alerts)
        self.session.navigate(1)
        self.assertIn("설명 확인", self.session.alerts[0]["message"])
        self.assertEqual(self.session.events[-2]["type"], "alert_shown")

    def test_incomplete_or_loss_never_claims_missing(self):
        self.feed("기기에서 음성을", endpoint="hard_max_duration")
        self.session.navigate(1)
        self.assertFalse(self.session.alerts)
        self.assertEqual(self.session.states["p1"]["status"], "uncertain")
        self.session.issue("입력 손실")
        self.session.stop()
        self.session.finish()
        self.assertEqual(self.session.states["p2"]["status"], "uncertain")

    def test_stale_job_and_invalid_output_cannot_mutate_state(self):
        first = self.feed("기기에서 음성을 인식합니다")
        second = self.feed("다음 설명입니다", 3, 4, "two")
        self.session.apply(first, self.coach.evaluate(first))
        self.assertEqual(self.session.states["p1"]["status"], "unconfirmed")
        response = self.coach.evaluate(second)
        response["judgments"][0]["evidence_segment_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            self.session.apply(second, response)
        self.assertEqual(self.session.states["p1"]["status"], "unconfirmed")

    def test_stop_freezes_timer_and_persists_full_evidence(self):
        job = self.feed("기기에서 음성을 인식합니다")
        self.session.stop()
        elapsed = self.session.elapsed()
        self.clock.advance(8)
        self.session.apply(job, self.coach.evaluate(job))
        self.session.finish()
        self.assertEqual(self.session.elapsed(), elapsed)
        with tempfile.TemporaryDirectory() as directory:
            path = self.session.save(directory)
            saved = json.loads(Path(path).read_text())
            self.assertEqual(saved["status"], "ended")
            self.assertEqual(saved["events"][-1]["type"], "session_ended")
            self.assertEqual(saved["segments"][0]["segment_id"], "one")

    def test_remote_model_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            LocalHttpCoach("https://example.com/coach")

    def test_future_segment_is_rejected_and_duplicate_is_ignored(self):
        with self.assertRaises(ValueError):
            self.session.ingest({"segment_id": "future", "text": "text", "start_sec": 2, "end_sec": 3})
        self.feed("설명입니다")
        self.assertIsNone(self.feed("설명입니다"))
        self.assertEqual(len(self.session.segments), 1)

    def test_decimal_punctuation_cannot_silently_change_a_number(self):
        deck = copy.deepcopy(DECK)
        deck["slides"][0]["keypoints"][0]["text"] = "정확도는 95.8%입니다"
        self.session = Session(deck, clock=self.clock)
        job = self.feed("정확도는 958%입니다")
        self.session.apply(job, self.coach.evaluate(job))
        self.assertEqual(self.session.states["p1"]["status"], "uncertain")

    def test_late_evidence_retracts_prior_missing_candidate(self):
        self.clock.advance(5)
        self.session.navigate(1)
        self.assertTrue(self.session.snapshot()["alerts"])
        job = self.feed("기기에서 음성을 인식합니다")
        self.session.apply(job, self.coach.evaluate(job))
        self.assertFalse(self.session.snapshot()["alerts"])

    def test_alert_expires_after_stop_while_timer_stays_frozen(self):
        self.clock.advance(5)
        self.session.stop()
        self.session.finish()
        self.assertTrue(self.session.snapshot()["alerts"])
        self.clock.advance(11)
        self.assertFalse(self.session.snapshot()["alerts"])
        self.assertEqual(self.session.elapsed(), 5)
        with tempfile.TemporaryDirectory() as directory:
            saved = json.loads(Path(self.session.save(directory)).read_text())
            self.assertTrue(saved["alerts"])
            self.assertFalse(saved["active_alerts"])


if __name__ == "__main__":
    unittest.main()
