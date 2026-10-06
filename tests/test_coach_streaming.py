"""Focused local-coach regressions; fake HTTP only, no model/audio execution."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ollama_coach import OllamaCoach
from src.script_coaching import ScriptSession, prepare_script
from tests.test_presentation import FakeClock


TEXT = Path(__file__).resolve().parents[1].joinpath("examples/presentation_script.txt").read_text() + "감사합니다.\n"
POINTS = prepare_script(TEXT, 90)["slides"][0]["keypoints"]


class FocusedCoachTests(unittest.TestCase):
    def setUp(self):
        self.coach = OllamaCoach("test:1b")
        self.coach.model_info = {"name": "test:1b"}
        self.requests = []

    def job(self, texts, *, tracking=True, confirmed=()):
        return {"slide": {"title": "대본", "keypoints": copy.deepcopy(POINTS)},
                "script_tracking": tracking, "confirmed_keypoint_ids": list(confirmed),
                "segments": [{"segment_id": f"s{index}", "text": text, "start_sec": index * 5,
                              "end_sec": index * 5 + 2, "endpoint_reason": "silence", "status": "OK"}
                             for index, text in enumerate(texts, 1)]}

    def evaluate(self, job, *, answers=None, extra=None):
        def request(url, body=None):
            payload = json.loads(body)
            self.requests.append(payload)
            compact = {key: (answers or {}).get(key, {"s": -1, "e": []})
                       for key in payload["format"]["required"]}
            compact.update(extra or {})
            return {"done": True, "done_reason": "stop", "message": {"content": json.dumps(compact)}}
        with patch.object(self.coach, "_request", side_effect=request):
            return self.coach.evaluate(job)

    def test_script_requests_latest_related_points_and_earliest_missed_anchor(self):
        job = self.job([POINTS[2]["text"], POINTS[3]["text"]], confirmed=["script-2", "script-3"])
        result = self.evaluate(job)
        requested = self.requests[-1]["format"]["required"]
        self.assertEqual(requested, ["P1", "P3", "P4"])
        self.assertEqual(self.coach.last_metrics["requested_keypoint_count"], 3)
        self.assertEqual(self.coach.last_metrics["input_keypoint_count"], 7)
        self.assertLess(self.requests[-1]["options"]["num_predict"], 1024)
        self.assertEqual(len(result["judgments"]), 7)
        self.assertEqual(result["judgments"][4]["status"], "unconfirmed")
        self.assertEqual(result["judgments"][4]["evidence_segment_ids"], [])
        self.assertEqual(self.requests[-1]["keep_alive"], "30m")

    def test_generic_slide_keeps_all_items_and_distinct_point_namespace(self):
        self.evaluate(self.job([POINTS[3]["text"]], tracking=False))
        self.assertEqual(self.requests[-1]["format"]["required"], [f"P{index}" for index in range(1, 8)])
        user = json.loads(self.requests[-1]["messages"][1]["content"])
        self.assertEqual(user["utterances"][0]["number"], 1)
        self.assertEqual(self.coach.last_metrics["requested_keypoint_count"], 7)

    def test_latest_correction_rechecks_an_already_confirmed_item(self):
        job = self.job(["발화를 최대 4초 구간으로 분할합니다.",
                        "앞서 4초는 잘못입니다. 발화를 최대 3초 구간으로 분할합니다."],
                       confirmed=["script-1", "script-2", "script-3"])
        result = self.evaluate(job, answers={"P3": {"s": 0, "e": [2]}})
        self.assertIn("P3", self.requests[-1]["format"]["required"])
        self.assertEqual(result["judgments"][2]["status"], "uncertain")
        self.assertEqual(result["judgments"][2]["evidence_segment_ids"], ["s2"])

    def test_logged_wrong_number_and_neighbor_citations_are_not_completion_evidence(self):
        job = self.job(["발자의 시간과 핵심 내용 설명을 돕습니다.", "음 성인인식은.",
                        "로컬 에서 진행 합니다.", "발화 를 최대 4초 구간 으로 분할 합니다.",
                        "불 확 실한 내 용은 판단 을 보류 합니다."],
                       confirmed=["script-1", "script-2", "script-3"])
        job["segments"][2].update(start_sec=12.5, end_sec=14)
        result = self.evaluate(job, answers={"P3": {"s": 1, "e": [2]}, "P4": {"s": 1, "e": [3]}})
        self.assertEqual(result["judgments"][2]["status"], "unconfirmed")
        self.assertEqual(result["judgments"][3]["status"], "unconfirmed")
        self.assertEqual(result["judgments"][4]["status"], "unconfirmed")
        self.assertFalse(result["judgments"][2]["evidence_segment_ids"])
        self.assertFalse(result["judgments"][3]["evidence_segment_ids"])
        self.assertEqual({entry["reason_code"] for entry in self.coach.last_metrics["guard_decisions"]},
                         {"missing_quantity_evidence", "unrelated_evidence"})

    def test_spoken_wrong_quantity_remains_uncertain_and_correct_source_still_confirms(self):
        bad = self.job(["발화를 최대 3초 구간으로 분할합니다."], confirmed=["script-1", "script-2"])
        result = self.evaluate(bad, answers={"P3": {"s": 1, "e": [1]}})
        self.assertEqual(result["judgments"][2]["status"], "uncertain")
        self.assertEqual(result["judgments"][2]["reason_code"], "quantity_mismatch")
        good = self.job(["발화 를 최대 4초 구간 으로 분할 합니다."], confirmed=["script-1", "script-2"])
        result = self.evaluate(good, answers={"P3": {"s": 1, "e": [1]}})
        self.assertEqual(result["judgments"][2]["status"], "explained")
        self.assertEqual(result["judgments"][2]["evidence_segment_ids"], ["s1"])

    def test_model_cannot_add_an_omitted_point_answer(self):
        job = self.job([POINTS[2]["text"]], confirmed=["script-1", "script-2"])
        with self.assertRaises(ValueError):
            self.evaluate(job, extra={"P7": {"s": 1, "e": [1]}})

    def test_wrong_citation_does_not_erase_session_previous_confirmed_quantity(self):
        clock = FakeClock()
        session = ScriptSession(prepare_script(TEXT, 90), clock=clock)
        clock.advance(20)
        first = session.ingest({"segment_id": "real-4", "text": POINTS[2]["text"],
                                "start_sec": 17, "end_sec": 19})
        session.apply(first, self.evaluate(first, answers={"P3": {"s": 1, "e": [1]}}))
        self.assertEqual(session.states["script-3"]["status"], "explained")
        clock.advance(5)
        second = session.ingest({"segment_id": "unrelated", "text": "음 성인인식은 로컬 에서 진행 합니다.",
                                 "start_sec": 22, "end_sec": 24})
        numeric_key = "P" + str(next(index for index, point in enumerate(second["slide"]["keypoints"], 1)
                                     if point["keypoint_id"] == "script-3"))
        result = self.evaluate(second, answers={numeric_key: {"s": 1, "e": [2]}})
        numeric = next(judgment for judgment in result["judgments"] if judgment["keypoint_id"] == "script-3")
        self.assertEqual(numeric["status"], "unconfirmed")
        self.assertTrue(any(entry["reason_code"] == "missing_quantity_evidence"
                            for entry in self.coach.last_metrics["guard_decisions"]))
        session.apply(second, result)
        self.assertEqual(session.states["script-3"]["status"], "explained")
        self.assertEqual(session.states["script-3"]["evidence_segment_ids"], ["real-4"])


class StartPreparationTests(unittest.TestCase):
    def test_every_start_uses_separate_dummy_warmup_and_keep_alive(self):
        coach = OllamaCoach("test:1b", timeout=6)
        coach.last_metrics = {"previous_user_inference": 1}
        requests = []

        def request(loader, url, body=None):
            if body is None:
                return {"models": [{"name": "test:1b", "size": 100, "digest": "fixture"}]}
            payload = json.loads(body)
            requests.append((loader.timeout, payload))
            return {"done": True, "done_reason": "stop", "message": {"content": '{"P1":{"s":1,"e":[1]}}'}}

        with patch.object(OllamaCoach, "_request", new=request):
            first = coach.prepare_for_start()
            second = coach.prepare_for_start()
        self.assertEqual(len(requests), 2)
        self.assertEqual(coach.timeout, 6)
        self.assertEqual(coach.last_metrics, {"previous_user_inference": 1})
        self.assertEqual(first["model_info"]["digest"], "fixture")
        self.assertEqual(second["prompt_version"], coach.prompt_version)
        for timeout, payload in requests:
            self.assertEqual(timeout, 60)
            self.assertEqual(payload["keep_alive"], "30m")
            user = json.loads(payload["messages"][1]["content"])
            self.assertEqual(user["slide_title"], "준비")
            self.assertEqual(user["utterances"], [{"number": 1, "text": "준비됐습니다"}])

    def test_prepare_failure_is_propagated_without_a_success_report(self):
        coach = OllamaCoach("test:1b")
        with patch.object(OllamaCoach, "_request", side_effect=TimeoutError("fake cold timeout")):
            with self.assertRaises(TimeoutError):
                coach.prepare_for_start()
        self.assertIsNone(coach.warm_up_metrics)
        self.assertIsNone(coach.model_info)


if __name__ == "__main__":
    unittest.main()
