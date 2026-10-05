"""Ordered script preparation and plan-relative progress; timing is never an LLM estimate."""
import copy
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
        # Each unit contributes once, at the end of the speech that first
        # established it. Model latency and re-reading never move this clock.
        self.confirmed_audio_ends = {}
        self.confirmed_judgment_delays = {}
        self.last_confirmed_audio_end = None
        self.latest_audio_end = None
        self.pace_input_uncertain = False
        self.pace_candidate = None
        self.pace_since = 0
        self.pace = "waiting"
        self.pace_last_notice = {}
        self.pace_notice_count = 0
        self.missing_ids = []
        self.missing_notice_counts = {}

    def ingest(self, segment):
        count = len(self.segments)
        job = super().ingest(segment)
        if len(self.segments) != count:
            item = self.segments[-1]
            if normalized(item["text"]) or item.get("status", "OK") != "OK":
                self.latest_audio_end = max(self.latest_audio_end or 0, item["end_sec"])
                self.pace_input_uncertain |= item.get("status", "OK") != "OK"
                if self.pace_input_uncertain:
                    self._review_script()
                # New speech might change or contradict the previous estimate.
                # Preserve a direction candidate during ordinary processing so
                # repeated short utterances can still establish a stable pace.
                self._hold_pace("new_speech_pending", reset=item.get("status", "OK") != "OK")
                self._review_script()
        return job

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
        job["script_tracking"] = True
        job["confirmed_keypoint_ids"] = [p["keypoint_id"] for p in points if self.states[p["keypoint_id"]]["status"] == "explained"]
        return job

    def apply(self, job, response):
        relevant = self.job_is_relevant(job)
        applied = super().apply(job, response)
        if not relevant:
            return []
        valid = {s["segment_id"]: s for s in job["segments"]}
        for kid in list(self.confirmed_audio_ends):
            if self.states[kid]["status"] != "explained":
                del self.confirmed_audio_ends[kid]
                del self.confirmed_judgment_delays[kid]
        for judgment in applied:
            kid = judgment["keypoint_id"]
            if self.states[kid]["status"] == "explained" and kid not in self.confirmed_audio_ends:
                ends = [valid[e]["end_sec"] for e in self.states[kid]["evidence_segment_ids"] if e in valid]
                if ends:
                    self.confirmed_audio_ends[kid] = max(ends)
                    self.confirmed_judgment_delays[kid] = max(0.0, self.elapsed() - max(ends))
        self.last_confirmed_audio_end = max(self.confirmed_audio_ends.values(), default=None)
        self._review_script()
        self._update_pace()
        return applied

    def issue(self, message):
        super().issue(message)
        # Loss/error evidence invalidates a gap immediately, including alerts
        # that were queued before the quality issue arrived.
        self._review_script()
        self._hold_pace("record_quality_issue", reset=True)

    def _hold_pace(self, reason, reset=False):
        self.pace = "waiting"
        if reset:
            self.pace_candidate, self.pace_since = None, self.elapsed()
        self._retract_pace_alerts(reason)

    def _retract_pace_alerts(self, reason, keep_direction=None):
        now = max(0.0, self.clock() - self.origin)
        for alert in self.alerts:
            if (alert["key"].startswith("pace:") and alert["expires_sec"] > now and
                    (keep_direction is None or alert["key"].split(":")[1] != keep_direction)):
                alert["expires_sec"] = now
                self._event("alert_retracted", key=alert["key"], reason=reason)

    def _review_script(self):
        points = self.plan["units"]
        explained = [i for i,p in enumerate(points) if self.states[p["keypoint_id"]]["status"] == "explained"]
        frontier = max(explained, default=-1)
        # Two later anchors, or an explained final unit, establish a passed section.
        candidates = [p["keypoint_id"] for i,p in enumerate(points) if i < frontier and
                      self.states[p["keypoint_id"]]["status"] == "unconfirmed" and
                      (sum(j > i for j in explained) >= 2 or frontier == len(points)-1)]
        quality_problem = bool(self.issues) or self.pace_input_uncertain
        processing = bool(self.pending) or any(r["pending"] for r in self.revisions.values())
        if quality_problem:
            # The presenter may have spoken a section that capture or inference
            # lost. Preserve confirmed content while withholding a missing claim.
            for kid in candidates:
                self.states[kid].update(status="uncertain", reason="기록 품질 문제가 있어 건너뛴 구간인지 확인이 필요합니다.")
            if candidates:
                self._event("judgment_deferred", reason="record_quality_issue", keypoint_ids=candidates)
            self.missing_ids = []
        elif processing:
            # An older completed sentence can advance progress immediately,
            # while the newer pending sentence may still fill an apparent gap.
            self.missing_ids = []
        else:
            self.missing_ids = candidates
        for kid in self.missing_ids:
            prefix = f"script_missing:{kid}"
            if any(a["key"].startswith("script_missing:") and a["key"].split(":")[1] == kid and
                   a["expires_sec"] > self.elapsed() for a in self.alerts):
                continue
            count = self.missing_notice_counts.get(kid, 0)
            key = prefix if count == 0 else f"{prefix}:{count+1}"
            self.alert(key, "건너뛴 설명을 확인해 주세요. " + next(p["text"] for p in points if p["keypoint_id"]==kid)[:140], 2)
            if key in self.alert_keys:
                self.missing_notice_counts[kid] = count+1
        for alert in self.alerts:
            if alert["key"].startswith("script_missing:") and alert["key"].split(":")[1] not in self.missing_ids:
                if alert["expires_sec"] > self.elapsed():
                    alert["expires_sec"] = self.elapsed()
                    self._event("alert_retracted", key=alert["key"], reason="record_quality_issue" if quality_problem else
                                "newer_judgment_pending" if processing else "script_evidence_changed")

    def progress(self):
        points = self.plan["units"]
        explained = [p for p in points if self.states[p["keypoint_id"]]["status"] == "explained"]
        amount = sum(p["units"] for p in explained)
        position = max((i for i,p in enumerate(points) if self.states[p["keypoint_id"]]["status"] == "explained"), default=-1)
        elapsed = self.elapsed()
        planned_sec = amount / self.plan["total_units"] * self.deck["total_duration_sec"]
        measured_sec = self.last_confirmed_audio_end
        ratio = planned_sec / measured_sec if measured_sec else None
        evidence_age = max(0.0, elapsed - measured_sec) if measured_sec is not None else None
        recent = evidence_age is not None and evidence_age <= 15
        processing_delay = max((self.confirmed_judgment_delays[kid] for kid,end in self.confirmed_audio_ends.items()
                                if end == measured_sec), default=None)
        passed_unresolved = any(self.states[p["keypoint_id"]]["status"] != "explained" for p in points[:position+1])
        processing = bool(self.pending) or any(r["pending"] for r in self.revisions.values())
        latest_unresolved = (self.latest_audio_end is not None and
                             (measured_sec is None or self.latest_audio_end > measured_sec + .001))
        quality_problem = bool(self.issues) or self.pace_input_uncertain
        if quality_problem:
            reason = "입력·처리 오류가 있어 속도 판단을 보류합니다."
        elif not recent:
            reason = "최근에 확인된 발화가 없어 속도 판단을 기다립니다."
        elif processing:
            reason = "새 발화를 처리하고 있습니다. 처리 지연은 발표 속도에 포함하지 않습니다."
        elif passed_unresolved:
            reason = "앞선 대본에 확인되지 않은 구간이 있어 속도 판단을 보류합니다."
        elif latest_unresolved:
            reason = "최근 발화에서 새 대본 진행을 확인할 때까지 속도 판단을 보류합니다."
        elif measured_sec < 15 or amount / self.plan["total_units"] < .1:
            reason = "발화 15초와 대본 10% 이상을 확인한 뒤 속도를 안내합니다."
        elif self.pace == "waiting":
            reason = "같은 속도 상태가 5초 이상 유지되는지 확인하고 있습니다."
        elif self.pace == "on_plan":
            reason = "목표 발표 시간에 맞는 속도로 진행하고 있습니다."
        else:
            reason = "목표 발표 시간보다 빠르게 진행하고 있습니다." if self.pace == "fast" else "목표 발표 시간보다 느리게 진행하고 있습니다."
        reliable = recent and not quality_problem and not processing and not passed_unresolved and not latest_unresolved
        return {"confirmed_units": amount, "total_units": self.plan["total_units"],
                "fraction": amount / self.plan["total_units"], "baseline_units_per_min": self.plan["baseline_units_per_min"],
                "observed_units_per_min": amount / measured_sec * 60 if measured_sec else None,
                "planned_elapsed_sec": planned_sec, "ratio": ratio, "reliable": reliable,
                "estimated_total_sec": measured_sec / (amount / self.plan["total_units"]) if amount and measured_sec else None,
                "measured_elapsed_sec": measured_sec, "plan_total_duration_sec": self.deck["total_duration_sec"],
                "processing_delay_sec": processing_delay, "evidence_age_sec": evidence_age,
                "pace": self.pace, "pace_reason": reason, "missing_ids": self.missing_ids,
                "position_index": position, "next_text": points[position+1]["text"] if position+1 < len(points) else None,
                "units": [{**p, **self.states[p["keypoint_id"]]} for p in points]}

    def tick(self):
        super().tick()
        if self.status != "running":
            return
        self._update_pace()

    def _update_pace(self):
        if self.status != "running":
            self._hold_pace("session_not_running", reset=True)
            return
        progress = self.progress()
        ratio = progress["ratio"]
        if not progress["reliable"]:
            # Only an ordinary, fresh inference wait preserves the candidate.
            # Errors, stale evidence, skipped content and unresolved new speech
            # must earn a new stable estimate before any advice is spoken.
            pending = bool(self.pending) or any(r["pending"] for r in self.revisions.values())
            fresh = (progress["measured_elapsed_sec"] is not None and
                     progress["evidence_age_sec"] <= 15)
            self._hold_pace("pace_evidence_pending" if pending else "pace_evidence_unreliable",
                            reset=bool(self.issues) or self.pace_input_uncertain or not (pending and fresh))
            return
        if progress["measured_elapsed_sec"] < 15 or progress["fraction"] < .1:
            self._hold_pace("pace_minimum_evidence", reset=True)
            return
        # Ignore floating-point rounding at the inclusive 25% boundaries.
        candidate = "fast" if ratio > 1.25 + 1e-9 else "slow" if ratio < .75 - 1e-9 else "on_plan"
        if candidate != self.pace_candidate:
            self.pace_candidate, self.pace_since = candidate, self.elapsed()
            self._retract_pace_alerts("pace_direction_changed")
        self.pace = candidate if self.elapsed() - self.pace_since >= 5 else "waiting"
        if self.pace == "waiting":
            return
        self._retract_pace_alerts("pace_direction_changed", keep_direction=self.pace)
        if self.pace in ("fast", "slow"):
            now = self.elapsed()
            if now - self.pace_last_notice.get(self.pace, -100) < 30:
                return
            message = "계획보다 빠릅니다. 조금 천천히 말씀해 주세요." if self.pace == "fast" else "계획보다 느립니다. 조금 더 빠르게 이어가 주세요."
            key = f"pace:{self.pace}:{self.pace_notice_count + 1}"
            self.alert(key, message, 1)
            if key in self.alert_keys:
                self.pace_last_notice[self.pace] = now
                self.pace_notice_count += 1

    def stop(self):
        super().stop()
        self._hold_pace("session_not_running", reset=True)

    def boundary(self, slide_id, version, notify=True):
        # Review script gaps from content evidence rather than a manual slide clock.
        self._review_script()
        if self.status == "stopping":
            remaining = [p for p in self.plan["units"] if self.states[p["keypoint_id"]]["status"] != "explained"]
            if remaining and notify:
                self.alert("script_final_review", "발표를 마쳤습니다. 확인되지 않은 대본 구간이 " + str(len(remaining)) + "개 있습니다.", 2)

    def snapshot(self):
        return {**super().snapshot(), "script_progress": self.progress()}
