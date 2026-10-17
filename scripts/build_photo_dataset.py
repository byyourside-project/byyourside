"""Build a reviewable synthetic draft of photo-zone function-call examples.

The output is not a validated training set. Human review and Maum token confirmation
are required before model training.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/photo_booth/dataset_v09_draft.json"
RNG = random.Random(20261017)
COUNTS = {"train": 60, "eval": 12, "test": 12}

NAMES = {
    ("select_layout", "single"): ["한 장 배치", "한 컷 배치", "사진 한 장 구성", "한 칸 화면", "한 장짜리 배치", "한 컷 화면", "한 장 모드", "한 칸짜리 구성"],
    ("select_layout", "four_cut"): ["네 컷 배치", "네 장 배치", "네 칸 화면", "네 컷 구성", "사진 네 장 구성", "네 칸짜리 배치", "네 컷 모드", "네 장짜리 구성"],
    ("set_frame", "plain"): ["기본 프레임", "장식 없는 테두리", "깔끔한 기본 테두리", "기본 사진틀", "꾸밈 없는 프레임", "심플한 테두리", "기본 디자인", "장식 없는 사진틀"],
    ("set_frame", "starlight"): ["별빛 프레임", "별빛 테두리", "별 장식 사진틀", "밤하늘 프레임", "반짝이는 별 테두리", "별빛 디자인", "별 모양 장식", "별이 있는 프레임"],
    ("set_frame", "party"): ["파티 프레임", "파티 테두리", "축제 느낌 사진틀", "파티 장식", "알록달록한 파티 프레임", "파티 디자인", "축제 테두리", "파티 분위기 프레임"],
    ("set_frame", "buddy"): ["별곰이 프레임", "별곰이 테두리", "별곰이 캐릭터 사진틀", "별곰이 장식", "곰 캐릭터 프레임", "별곰이 디자인", "별곰이 그림 테두리", "별곰이 사진틀"],
    ("set_timer", 3): ["삼 초 타이머", "세 초 타이머", "삼 초 대기", "세 초 대기", "삼 초 촬영 타이머", "세 초 촬영 대기", "삼 초", "세 초"],
    ("set_timer", 10): ["십 초 타이머", "열 초 타이머", "십 초 대기", "열 초 대기", "십 초 촬영 타이머", "열 초 촬영 대기", "십 초", "열 초"],
    ("set_timer", 15): ["십오 초 타이머", "열다섯 초 타이머", "십오 초 대기", "열다섯 초 대기", "십오 초 촬영 타이머", "열다섯 초 촬영 대기", "십오 초", "열다섯 초"],
    ("set_timer", 20): ["이십 초 타이머", "스무 초 타이머", "이십 초 대기", "스무 초 대기", "이십 초 촬영 타이머", "스무 초 촬영 대기", "이십 초", "스무 초"],
    ("set_shooting_view", "strip"): ["네 칸에서 촬영하는 방식", "네 칸 안에서 찍는 방법", "네 컷 화면 촬영 방식", "네 칸 미리보기 촬영", "네 칸을 보면서 찍는 방식", "사진 띠 안에서 촬영하기", "네 칸 화면 보기", "네 컷 틀 안에서 촬영하기"],
    ("set_shooting_view", "full"): ["한 컷씩 크게 보는 방식", "큰 화면 촬영 방식", "한 장씩 전체 화면으로 보는 방법", "풀 화면 촬영 방식", "한 컷씩 크게 촬영하기", "큰 카메라 화면 보기", "한 장씩 크게 보는 촬영", "전체 화면에서 한 컷씩 찍기"],
}

VIEW_PHRASES = {
    "zoom_in": ["화면 확대해 줘", "화면을 확대해 줘", "구도 확대해 줘", "카메라 화면 확대해 줘", "화면 좀 확대해 줘", "화면을 더 확대해 줘", "구도를 확대해 주세요", "화면 줌인해 줘"],
    "zoom_out": ["화면 축소해 줘", "화면을 축소해 줘", "구도 축소해 줘", "카메라 화면 축소해 줘", "화면 좀 축소해 줘", "화면을 더 축소해 줘", "구도를 축소해 주세요", "화면 줌아웃해 줘"],
    "move_left": ["구도를 왼쪽으로 옮겨 줘", "화면을 왼쪽으로 이동해 줘", "보이는 위치를 왼쪽으로 움직여 줘", "화면 위치를 왼쪽으로 맞춰 줘", "구도 왼쪽으로 옮겨 줘", "화면을 왼쪽으로 옮겨 주세요", "구도를 왼쪽으로 이동해 줘", "보이는 화면을 왼쪽으로 움직여 줘"],
    "move_right": ["구도를 오른쪽으로 옮겨 줘", "화면을 오른쪽으로 이동해 줘", "보이는 위치를 오른쪽으로 움직여 줘", "화면 위치를 오른쪽으로 맞춰 줘", "구도 오른쪽으로 옮겨 줘", "화면을 오른쪽으로 옮겨 주세요", "구도를 오른쪽으로 이동해 줘", "보이는 화면을 오른쪽으로 움직여 줘"],
    "move_up": ["구도를 위로 옮겨 줘", "화면을 위쪽으로 이동해 줘", "보이는 위치를 위로 움직여 줘", "화면 위치를 위쪽으로 맞춰 줘", "구도 위로 옮겨 줘", "화면을 위로 옮겨 주세요", "구도를 위쪽으로 이동해 줘", "보이는 화면을 위로 움직여 줘"],
    "move_down": ["구도를 아래로 옮겨 줘", "화면을 아래쪽으로 이동해 줘", "보이는 위치를 아래로 움직여 줘", "화면 위치를 아래쪽으로 맞춰 줘", "구도 아래로 옮겨 줘", "화면을 아래로 옮겨 주세요", "구도를 아래쪽으로 이동해 줘", "보이는 화면을 아래로 움직여 줘"],
    "reset": ["화면 구도 초기화해 줘", "구도를 원래대로 해 줘", "화면을 원래대로 돌려 줘", "배율을 초기화해 줘", "구도 초기화해 주세요", "화면 확대를 원래대로 해 줘", "화면 위치를 초기화해 줘", "배율과 구도를 초기화해 줘"],
}

PATTERNS = {
    "train": ["{x}{ro} 바꿔 줘", "{x}{ro} 설정해 줘", "{x}{ro} 해 줘", "{x}{ro} 맞춰 줘", "{x}{ro} 변경해 줘", "{x}{ro} 바꿔 주세요", "{x}{ro} 설정해 주세요", "이번에는 {x}{ro} 해 줘", "촬영 전에 {x}{ro} 바꿔 줘", "일단 {x}{ro} 맞춰 줘", "{x}{ro} 부탁해", "지금 {x}{ro} 설정해 줘"],
    "eval": ["{x}{eul} 선택해 줘", "다음 사진은 {x}{ro} 부탁해", "{x}{eul} 적용해 줄래"],
    "test": ["{x}{eul} 쓰고 싶어", "{x}{eul} 골라 줘", "이번 촬영은 {x}{ro} 할래"],
}

CAPTURE_BASE = [
    "사진 찍어 줘", "사진 촬영해 줘", "카메라 셔터 눌러 줘", "촬영 시작해 줘",
    "이제 찍어 줘", "이제 사진 찍어 줘", "한 번 촬영해 줘", "셔터 눌러 줘",
    "찰칵 찍어 줘", "사진 한 번 찍어 줘", "지금 촬영해 줘", "지금 사진 찍어 줘",
    "카메라로 찍어 줘", "촬영 버튼 눌러 줘", "셔터 작동시켜 줘", "찍어 주세요",
    "사진 촬영 시작해 줘", "이제 셔터 눌러 줘", "바로 사진 찍어 줘", "이 자리에서 촬영해 줘",
]
CAPTURE_PATTERNS = {
    "train": ["{x}", "부탁할게 {x}", "여기서 {x}", "우리 준비됐어 {x}", "포즈 잡았어 {x}", "카메라 앞에 섰어 {x}", "다 같이 준비됐어 {x}", "이 위치에서 {x}"],
    "eval": ["준비 다 했으니 {x}", "이 자세로 {x}"],
    "test": ["우리 모두 자리 잡았어 {x}", "촬영 위치에 있으니 {x}"],
}

AMBIGUOUS = ["좀 예쁘게 해 줘", "사진 느낌 바꿔 줘", "좀 더 좋게 해 줘", "이거 다르게 해 줘", "화면 좀 바꿔 줘", "마음에 들게 해 줘", "알아서 해 줘", "좀 손봐 줘", "분위기 맞춰 줘", "이대로 괜찮을까", "어떻게 하면 좋을까", "더 멋있게 해 줘", "설정 좀 해 줘", "이거 골라 줘", "좀 특별하게 해 줘"]
UNSUPPORTED = ["사진을 저장해 줘", "사진을 인쇄해 줘", "사진을 문자로 보내 줘", "사진을 삭제해 줘", "결과를 다시 보여 줘", "사진을 재촬영해 줘", "사진을 편집해 줘", "영상을 촬영해 줘", "사진을 이메일로 보내 줘", "사진을 공유해 줘", "사진을 크게 확대해 줘", "사진을 클라우드에 올려 줘", "결제를 취소해 줘", "사진을 다운로드해 줘", "방금 결과를 다시 띄워 줘"]
NEG_PATTERNS = {
    "train": ["{x}", "저기 {x}", "부탁인데 {x}", "혹시 {x}", "여기서 {x}", "지금 {x}"],
    "eval": ["내가 원하는 건 {x}", "이용하는 동안 {x}"],
    "test": ["이 화면에서 {x}", "가능하면 {x}"],
}
FIRST_ACTIONS = ["한 장 배치로 바꾸고", "네 컷 배치로 바꾸고", "별빛 프레임으로 바꾸고", "파티 프레임으로 바꾸고", "기본 프레임으로 바꾸고", "별곰이 프레임으로 바꾸고", "십 초 타이머로 맞추고", "삼 초 타이머로 맞추고", "이십 초 타이머로 맞추고", "큰 화면 촬영 방식으로 바꾸고", "네 칸 촬영 방식으로 바꾸고", "열다섯 초 타이머로 맞추고"]
SECOND_ACTIONS = ["사진 찍어 줘", "촬영해 줘", "셔터 눌러 줘", "파티 프레임도 선택해 줘", "네 컷 배치도 설정해 줘", "십 초 타이머도 맞춰 줘", "별빛 프레임도 골라 줘", "한 장 배치도 선택해 줘"]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def particles(noun: str) -> dict[str, str]:
    last = noun[-1]
    jong = (ord(last) - 0xAC00) % 28 if "가" <= last <= "힣" else 0
    return {"ro": "로" if jong in (0, 8) else "으로", "eul": "을" if jong else "를"}


def candidates(key: tuple[str, object], split: str) -> list[str]:
    action, value = key
    if action == "adjust_view":
        prefixes = {"train": ["", "지금 ", "여기서 ", "이번에는 ", "조금 "], "eval": ["이제 "], "test": ["부탁인데 "]}[split]
        return [normalize(prefix + phrase) for prefix in prefixes for phrase in VIEW_PHRASES[value]]
    if action in {"select_layout", "set_frame", "set_timer", "set_shooting_view"}:
        return [normalize(p.format(x=x, **particles(x))) for p in PATTERNS[split] for x in NAMES[key]]
    if action == "capture_photo":
        return [normalize(p.format(x=x)) for p in CAPTURE_PATTERNS[split] for x in CAPTURE_BASE]
    if value in {"ambiguous", "unsupported"}:
        bases = AMBIGUOUS if value == "ambiguous" else UNSUPPORTED
        return [normalize(p.format(x=x)) for p in NEG_PATTERNS[split] for x in bases]
    if value == "multi_action":
        pairs = []
        for first in FIRST_ACTIONS:
            for second in SECOND_ACTIONS:
                if "프레임" in first and "프레임" in second:
                    continue
                if "배치" in first and "배치" in second:
                    continue
                if "타이머" in first and "타이머" in second:
                    continue
                join = " " if split == "train" else " 그다음 " if split == "eval" else " 이어서 "
                pairs.append(normalize(f"{first}{join}{second}"))
        return pairs
    raise ValueError(key)


def wanted(split: str, key: tuple[str, object]) -> int:
    action = key[0]
    if action == "adjust_view":
        return {"train": 40, "eval": 8, "test": 8}[split]
    if action == "capture_photo":
        return {"train": 100, "eval": 20, "test": 20}[split]
    return COUNTS[split]


def build() -> list[dict]:
    seed = json.loads((ROOT / "docs/photo_booth/seed_examples.json").read_text())
    keys = list(NAMES) + [("adjust_view", x) for x in VIEW_PHRASES] + [("capture_photo", None)] + [("no_action", x) for x in ("ambiguous", "unsupported", "multi_action")]
    rows: list[dict] = []
    seen: set[str] = set()
    for split in ("train", "eval", "test"):
        for key in keys:
            action, value = key
            arg_key = {"select_layout": "layout", "set_frame": "frame", "set_timer": "seconds", "set_shooting_view": "view", "adjust_view": "adjustment", "no_action": "reason"}.get(action)
            originals = [x for x in seed if x["split"] == split and x["action"] == action and (arg_key is None or x["args"].get(arg_key) == value)]
            # The seed is retained when it has a matching action and value.
            if action == "capture_photo":
                originals = [x for x in seed if x["split"] == split and x["action"] == action]
            selected = []
            for old in originals:
                phrase = normalize(old["utterance"])
                if phrase not in seen:
                    selected.append(phrase)
                    seen.add(phrase)
            pool = candidates(key, split)
            RNG.shuffle(pool)
            for phrase in pool:
                if len(selected) >= wanted(split, key):
                    break
                if phrase not in seen:
                    selected.append(phrase)
                    seen.add(phrase)
            assert len(selected) == wanted(split, key), (split, key, len(selected))
            args = {} if arg_key is None else {arg_key: value}
            for phrase in selected:
                rows.append({"id": "", "split": split, "utterance": phrase, "action": action, "args": args, "source": "synthetic_draft", "review_status": "needs_human_review"})
    counts = Counter(x["split"] for x in rows)
    assert counts == {"train": 1280, "eval": 256, "test": 256}, counts
    for split, prefix in (("train", "T"), ("eval", "E"), ("test", "S")):
        number = 0
        for row in rows:
            if row["split"] == split:
                number += 1
                row["id"] = f"{prefix}{number:04d}"
    assert len(seen) == len(rows)
    forbidden = {"네 컷으로 찍어줘", "삼 초 뒤에 찍어줘", "다시 찍어줘"}
    assert not forbidden.intersection(seen)
    return rows


if __name__ == "__main__":
    records = build()
    OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {OUTPUT} ({len(records)} rows)")
    print(Counter((x["split"], x["action"]) for x in records))
