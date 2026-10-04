"""Ordered script preparation and plan-relative progress; timing is never an LLM estimate."""
import copy
import math
import re
from difflib import SequenceMatcher
from src.presentation import Session, normalized, positive_number, validate_deck


def prepare_script(text, duration, title="대본 발표"):
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError("대본은 1~20,000자로 입력해 주세요.")
    duration = positive_number(duration, "목표 시간")
    # A line or sentence is one review unit; keep the user's full original text.
    units = [s.strip() for s in re.split(r"\n+|(?<=[.!?。！？])\s+", text) if normalized(s)]
    if not units or len(units) > 50 or any(len(s) > 1000 for s in units):
        raise ValueError("대본은 최대 50개 문장/줄, 각 구간 1,000자 이하로 나눠 주세요.")
    weights = [len(normalized(s)) for s in units]
    total = sum(weights)
    points = [{"keypoint_id": f"script-{i+1}", "text": s, "required": True} for i,s in enumerate(units)]
    deck = validate_deck({"deck_id": "user-script", "version": 2, "title": title,
                         "total_duration_sec": duration, "script_text": text,
                         "slides": [{"slide_id": "script", "title": title, "target_duration_sec": duration, "keypoints": points}]})
    end = 0
    plan = []
    for point, weight in zip(points, weights):
        start = end
        end += duration * weight / total
        plan.append({"keypoint_id": point["keypoint_id"], "text": point["text"], "units": weight,
                     "planned_start_sec": start, "planned_end_sec": end})
    deck["script_plan"] = {"version": 1, "unit": "공백·문장부호 제외 글자", "total_units": total,
                           "baseline_units_per_min": total / duration * 60, "units": plan}
    return deck


class ScriptSession(Session):
    def __init__(self, deck, **kwargs):
        super().__init__(prepare_script(deck["script_text"], deck["total_duration_sec"], deck["title"]), **kwargs)
        self.plan = self.deck["script_plan"]
        self.last_confirmed_audio_end = None
        self.pace_candidate = None
        self.pace_since = 0
        self.pace = "waiting"
        self.missing_ids = []

    def _pending_job(self, version):
        job = super()._pending_job(version)
        # Limit inference context while still routing a jump by lexical similarity.
        job["slide"] = copy.deepcopy(job["slide"])
        points = job["slide"]["keypoints"]
        confirmed = [i for i,p in enumerate(points) if self.states[p["keypoint_id"]]["status"] == "explained"]
        cursor = max(confirmed, default=-1)
        text = normalized(" ".join(s["text"] for s in job["segments"][-3:]))
        ranked = sorted(range(len(points)), key=lambda i: SequenceMatcher(None, normalized(points[i]["text"]), text).ratio(), reverse=True)[:2]
        indices = set(range(max(0,cursor-1), min(len(points),cursor+7))) | set(ranked)
        job["slide"]["keypoints"] = [p for i,p in enumerate(points) if i in indices]
        return job

    def apply(self, job, response):
        current = self.job_is_current(job)
        super().apply(job, response)
        if not current:
            return
        valid = {s["segment_id"]: s for s in job["segments"]}
        ends = [valid[e]["end_sec"] for j in response["judgments"] if j["status"] == "explained" for e in j["evidence_segment_ids"]]
        if ends:
            self.last_confirmed_audio_end = max(ends)
        self._review_script()

    def issue(self, message):
        super().issue(message)
        # Loss/error evidence invalidates a gap immediately, including alerts
        # that were queued before the quality issue arrived.
        self._review_script()

    def _review_script(self):
        points = self.plan["units"]
        explained = [i for i,p in enumerate(points) if self.states[p["keypoint_id"]]["status"] == "explained"]
        frontier = max(explained, default=-1)
        # Two later anchors, or an explained final unit, establish a passed section.
        candidates = [p["keypoint_id"] for i,p in enumerate(points) if i < frontier and
                      self.states[p["keypoint_id"]]["status"] == "unconfirmed" and
                      (sum(j > i for j in explained) >= 2 or frontier == len(points)-1)]
        if self.issues:
            # The presenter may have spoken a section that capture or inference
            # lost. Preserve confirmed content while withholding a missing claim.
            for kid in candidates:
                self.states[kid].update(status="uncertain", reason="기록 품질 문제가 있어 건너뛴 구간인지 확인이 필요합니다.")
            if candidates:
                self._event("judgment_deferred", reason="record_quality_issue", keypoint_ids=candidates)
            self.missing_ids = []
        else:
            self.missing_ids = candidates
        for kid in self.missing_ids:
            self.alert(f"script_missing:{kid}", "건너뛴 설명을 확인해 주세요. " + next(p["text"] for p in points if p["keypoint_id"]==kid)[:140], 2)
        for alert in self.alerts:
            if alert["key"].startswith("script_missing:") and alert["key"].split(":",1)[1] not in self.missing_ids:
                if alert["expires_sec"] > self.elapsed():
                    alert["expires_sec"] = self.elapsed()
                    self._event("alert_retracted", key=alert["key"], reason="record_quality_issue" if self.issues else "script_evidence_changed")

    def progress(self):
        points = self.plan["units"]
        explained = [p for p in points if self.states[p["keypoint_id"]]["status"] == "explained"]
        amount = sum(p["units"] for p in explained)
        position = max((i for i,p in enumerate(points) if self.states[p["keypoint_id"]]["status"] == "explained"), default=-1)
        elapsed = self.elapsed()
        planned_sec = amount / self.plan["total_units"] * self.deck["total_duration_sec"]
        ratio = planned_sec / elapsed if elapsed else None
        recent = self.last_confirmed_audio_end is not None and elapsed - self.last_confirmed_audio_end <= 15
        passed_unresolved = any(self.states[p["keypoint_id"]]["status"] != "explained" for p in points[:position+1])
        reliable = recent and not self.issues and not self.pending and not any(r["pending"] for r in self.revisions.values()) and not passed_unresolved
        return {"confirmed_units": amount, "total_units": self.plan["total_units"],
                "fraction": amount / self.plan["total_units"], "baseline_units_per_min": self.plan["baseline_units_per_min"],
                "observed_units_per_min": amount / elapsed * 60 if elapsed else None,
                "planned_elapsed_sec": planned_sec, "ratio": ratio, "reliable": reliable,
                "estimated_total_sec": elapsed / (amount / self.plan["total_units"]) if amount else None,
                "pace": self.pace, "missing_ids": self.missing_ids,
                "position_index": position, "next_text": points[position+1]["text"] if position+1 < len(points) else None,
                "units": [{**p, **self.states[p["keypoint_id"]]} for p in points]}

    def tick(self):
        super().tick()
        if self.status != "running":
            return
        progress = self.progress()
        ratio = progress["ratio"]
        candidate = ("fast" if ratio > 1.25 else "slow" if ratio < .75 else "on_plan") if progress["reliable"] and self.elapsed() >= 15 and progress["fraction"] >= .1 else "waiting"
        if candidate != self.pace_candidate:
            self.pace_candidate, self.pace_since = candidate, self.elapsed()
        self.pace = candidate if self.elapsed() - self.pace_since >= 5 else "waiting"
        if self.pace in ("fast", "slow"):
            bucket = math.floor(self.elapsed()/30)
            message = "계획보다 빠르게 진행하고 있습니다. 조금 천천히 말씀해 주세요." if self.pace == "fast" else "계획보다 느리게 진행하고 있습니다. 남은 내용을 조금 더 빠르게 이어가 주세요."
            self.alert(f"pace:{self.pace}:{bucket}", message, 1)

    def boundary(self, slide_id, version, notify=True):
        # Review script gaps from content evidence rather than a manual slide clock.
        self._review_script()
        if self.status == "stopping":
            remaining = [p for p in self.plan["units"] if self.states[p["keypoint_id"]]["status"] != "explained"]
            if remaining and notify:
                self.alert("script_final_review", "발표를 마쳤습니다. 확인되지 않은 대본 구간이 " + str(len(remaining)) + "개 있습니다.", 2)

    def snapshot(self):
        return {**super().snapshot(), "script_progress": self.progress()}
