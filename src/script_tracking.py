"""Script location and slide clocks, independent of factual completion."""
import copy
from src.presentation import normalized


class ScriptTracking:
    def _init_tracking(self):
        self.cursor = 0
        self.cursor_phase = "waiting"
        self.position_end = -1.0
        self.position_revision = 0
        self.position_epoch = 0
        self.position_after = 0.0
        self.position_misses = 0
        self.position_reason = "첫 문장부터 현재 위치를 확인합니다."
        self.script_slide_index = 0
        self.slide_records = []
        self.script_visit = {"number": 1, "slide_index": 0, "start_sec": 0.0,
                             "start_source": "session_start", "start_reliable": True}

    def _tracking_job(self, job):
        job["slide"] = copy.deepcopy(job["slide"])
        points = self.deck["slides"][0]["keypoints"]
        forward = set(range(self.cursor, min(len(points), self.cursor + 4)))
        recovery = self.position_misses >= 2
        if recovery:
            # Broaden only after repeated misses; never search the entire deck.
            forward |= {i for i, p in enumerate(self.plan["units"])
                        if p["slide_index"] == self.script_slide_index and i >= self.cursor}
        indices = forward | set(range(max(0, self.cursor - 2), self.cursor))
        job["slide"]["keypoints"] = copy.deepcopy([p for i, p in enumerate(points) if i in indices])
        job.update(script_tracking=True, script_position_ids=[points[i]["keypoint_id"] for i in sorted(forward)],
                   script_cursor_id=points[self.cursor]["keypoint_id"], position_epoch=self.position_epoch,
                   script_recovery=recovery,
                   confirmed_keypoint_ids=[p["keypoint_id"] for p in points if self.states[p["keypoint_id"]]["status"] == "explained"])
        return job

    def job_is_current(self, job):
        return job.get("position_epoch", self.position_epoch) == self.position_epoch and super().job_is_current(job)

    def job_is_relevant(self, job):
        return job.get("position_epoch", self.position_epoch) == self.position_epoch and super().job_is_relevant(job)

    def _validate_position(self, job, response):
        position = response.get("position") if isinstance(response, dict) else None
        if position is None:
            return None  # Legacy/local phrase adapters: confirmed facts only.
        if not isinstance(position, dict) or set(position) != {"keypoint_id", "phase", "evidence_segment_ids"}:
            raise ValueError("대본 위치 응답 형식이 올바르지 않습니다.")
        kid, phase, evidence = position["keypoint_id"], position["phase"], position["evidence_segment_ids"]
        if phase not in ("none", "ambiguous", "partial", "complete") or not isinstance(evidence, list):
            raise ValueError("대본 위치 상태가 올바르지 않습니다.")
        ids = {s["segment_id"] for s in job["segments"]}
        if any(not isinstance(e, str) or e not in ids for e in evidence):
            raise ValueError("대본 위치 근거 발화가 존재하지 않습니다.")
        if phase in ("none", "ambiguous"):
            if kid is not None or evidence:
                raise ValueError("불명확한 위치는 문장을 선택할 수 없습니다.")
        elif kid not in job.get("script_position_ids", []) or not evidence:
            raise ValueError("대본 위치가 후보 범위를 벗어났거나 근거가 없습니다.")
        judgments = response.get("judgments", [])
        if kid is not None and isinstance(judgments, list) and any(
                isinstance(j, dict) and j.get("keypoint_id") == kid and j.get("status") == "unconfirmed" for j in judgments):
            return {"keypoint_id": None, "phase": "none", "evidence_segment_ids": []}
        return position

    def _apply_position(self, job, position, applied):
        latest = max(job["segments"], key=lambda s: s["end_sec"])
        # Speech arriving during inference must not starve tracking. Only a
        # response older than an already-applied location is stale.
        if (latest["end_sec"] < self.position_end or latest["start_sec"] < self.position_after or
                job["revision"] <= self.position_revision):
            return
        self.position_revision = job["revision"]
        if position is None:
            matches = [j for j in applied if j["status"] == "explained" and
                       j["keypoint_id"] in job.get("script_position_ids", []) and
                       latest["segment_id"] in j["evidence_segment_ids"]]
            position = ({"keypoint_id": matches[0]["keypoint_id"], "phase": "complete",
                         "evidence_segment_ids": matches[0]["evidence_segment_ids"]} if len(matches) == 1
                        else {"keypoint_id": None, "phase": "ambiguous" if matches else "none", "evidence_segment_ids": []})
        evidence = position["evidence_segment_ids"]
        if position["keypoint_id"] is None or latest["segment_id"] not in evidence:
            self.position_misses += 1
            self.position_reason = "현재 위치가 불명확합니다. 다음 발화를 기다리거나 문장을 직접 선택해 주세요."
            self._event("script_position_held", phase=position["phase"], misses=self.position_misses)
            return
        points = self.plan["units"]
        index = next(i for i, p in enumerate(points) if p["keypoint_id"] == position["keypoint_id"])
        if index < self.cursor:
            return
        # A repeated sentence must not create a false jump even if a model picks
        # the later duplicate. A manual resync can disambiguate these passages.
        duplicates = [p for p in job["slide"]["keypoints"]
                      if normalized(p["text"]) == normalized(points[index]["text"])]
        if len(duplicates) > 1 and index != self.cursor:
            self.position_misses += 1
            self.position_reason = "같은 문장이 반복되어 위치를 확정할 수 없습니다."
            return
        target_slide = points[index]["slide_index"]
        if target_slide != self.script_slide_index:
            old_ids = set(self.plan["slides"][self.script_slide_index]["keypoint_ids"])
            mixed = any(j["keypoint_id"] in old_ids and j["status"] != "unconfirmed" and
                        set(evidence) & set(j["evidence_segment_ids"]) for j in applied)
            start = min(s["start_sec"] for s in job["segments"] if s["segment_id"] in evidence)
            consecutive = target_slide == self.script_slide_index + 1
            reliable = consecutive and not mixed and not self.issues and start >= self.script_visit["start_sec"]
            self._change_script_slide(target_slide, max(start, self.script_visit["start_sec"]), "next_slide_speech", reliable)
        self.cursor = index
        self.cursor_phase = position["phase"]
        self.position_end = latest["end_sec"]
        self.position_misses = 0
        self.position_reason = "발화 근거로 현재 문장을 찾았습니다. 내용 확인 결과는 별도로 표시합니다."
        self._event("script_position", index=index, phase=self.cursor_phase, evidence_segment_ids=evidence,
                    audio_end_sec=self.position_end)

    def _close_script_slide(self, end, source, reliable, notify=True):
        visit = self.script_visit
        slide = self.plan["slides"][visit["slide_index"]]
        actual = max(0.0, end - visit["start_sec"])
        reliable = bool(reliable and visit["start_reliable"] and end > visit["start_sec"])
        ratio = slide["target_duration_sec"] / actual if actual > 0 else None
        pace = self._classify_display_ratio(ratio) if reliable else "waiting"
        record = {**visit, "end_sec": end, "end_source": source, "actual_duration_sec": actual,
                  "target_duration_sec": slide["target_duration_sec"], "reliable": reliable,
                  "estimated": source == "next_slide_speech" or visit["start_source"] == "next_slide_speech",
                  "delta_sec": actual - slide["target_duration_sec"], "pace": pace,
                  "schedule_delta_sec": end - slide["planned_end_sec"],
                  "unconfirmed_ids": [kid for kid in slide["keypoint_ids"] if self.states[kid]["status"] != "explained"]}
        self.slide_records.append(record)
        self._event("script_slide_review", **record)
        if notify and reliable and pace in ("fast", "slow") and self.status == "running":
            difference = max(1, round(abs(record["delta_sec"])))
            message = f"직전 {visit['slide_index']+1}번 슬라이드를 계획보다 {difference}초 " + ("일찍 마쳤습니다." if pace == "fast" else "늦게 마쳤습니다.")
            self.alert(f"slide_pace:{pace}:{visit['number']}", message, 4, ttl=12)

    def _change_script_slide(self, index, at, source, reliable):
        for alert in self.alerts:
            if alert["key"].startswith("slide_pace:"):
                alert["expires_sec"] = min(alert["expires_sec"], self.elapsed())
        self._close_script_slide(at, source, reliable)
        self.script_slide_index = index
        self.script_visit = {"number": self.script_visit["number"] + 1, "slide_index": index,
                             "start_sec": at, "start_source": source, "start_reliable": reliable}
        self._event("script_slide_changed", **self.script_visit)

    def resync(self, index, *, slide_transition=False):
        if self.status != "running":
            raise ValueError("진행 중에만 대본 위치를 변경할 수 있습니다.")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.plan["units"]):
            raise ValueError("대본 문장 범위를 벗어났습니다.")
        at = self.elapsed()
        target = self.plan["units"][index]["slide_index"]
        if target != self.script_slide_index:
            self._change_script_slide(target, at, "manual" if slide_transition else "manual_resync", slide_transition)
        self.cursor, self.cursor_phase = index, "manual"
        self.position_epoch += 1
        self.position_after = at
        self.position_end = -1
        self.position_misses = 0
        self.position_reason = "선택한 문장에서 추적을 다시 시작합니다."
        # Invalidate queued/in-flight snapshots and partial audio across resync.
        self.pending.clear()
        for revision in self.revisions.values():
            revision["pending"] = False
        self._event("script_position_reset", index=index, epoch=self.position_epoch)

    def navigate(self, index):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.plan["slides"]):
            raise ValueError("슬라이드 범위를 벗어났습니다.")
        first = self.plan["slides"][index]["keypoint_ids"][0]
        self.resync(next(i for i, p in enumerate(self.plan["units"]) if p["keypoint_id"] == first), slide_transition=True)

    def finish(self):
        if self.status == "ended":
            return
        super().finish()
        # The stop clock is frozen before pending STT/LLM results drain.
        self._close_script_slide(self.ended_sec, "session_end", True, notify=False)

    def tracking_snapshot(self):
        return {"index": self.cursor, "phase": self.cursor_phase, "reason": self.position_reason,
                "misses": self.position_misses, "recovery": self.position_misses >= 2,
                "slide_index": self.script_slide_index, "audio_end_sec": self.position_end}

    def slide_timing_snapshot(self):
        slide = self.plan["slides"][self.script_slide_index]
        spent = max(0.0, self.elapsed() - self.script_visit["start_sec"])
        rows = [p for p in self.plan["units"] if p["slide_index"] == self.script_slide_index]
        # The current sentence contributes only when fully spoken; correctness
        # is intentionally not used to locate the presenter in the time plan.
        completed = [p for p in rows if self.plan["units"].index(p) < self.cursor or
                     (self.plan["units"].index(p) == self.cursor and self.cursor_phase == "complete")]
        planned = sum(p["planned_duration_sec"] for p in completed)
        measured = self.position_end - self.script_visit["start_sec"]
        fresh = self.position_end >= 0 and self.elapsed() - self.position_end <= 15
        reliable = bool(self.script_visit["start_reliable"] and fresh and not self.position_misses and
                        not self.pace_input_uncertain and not self.issues and measured > 0 and planned > 0)
        ratio = planned / measured if reliable else None
        return {"slide_index": self.script_slide_index, "elapsed_sec": spent,
                "target_duration_sec": slide["target_duration_sec"], "planned_elapsed_sec": planned,
                "measured_elapsed_sec": measured if reliable else None,
                "pace": self._classify_display_ratio(ratio), "reliable": reliable,
                "ratio": ratio, "estimated": True, "records": copy.deepcopy(self.slide_records),
                "schedule_delta_sec": self.position_end - (slide["planned_start_sec"] + planned) if reliable else None}
