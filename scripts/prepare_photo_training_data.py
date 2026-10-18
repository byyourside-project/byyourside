"""Export the reviewed photo-booth draft to a local LoRA pilot format.

This is a local experiment format, not Maum AI's unpublished training format.
The output strings are labels; running this script does not add tokenizer tokens.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/photo_booth/dataset_v09_draft.json"
DESTINATION = ROOT / "outputs/photo_booth/local_training_v09"

VALUES = {
    "select_layout": ("layout", {"single": 1, "four_cut": 2}, 0),
    "set_frame": ("frame", {"plain": 1, "starlight": 2, "party": 3, "buddy": 4}, 1),
    "set_timer": ("seconds", {3: 3, 10: 10, 15: 15, 20: 20}, 2),
    "no_action": ("reason", {"ambiguous": 1, "unsupported": 2, "multi_action": 3}, 4),
    "set_shooting_view": ("view", {"strip": 1, "full": 2}, 5),
    "adjust_view": ("adjustment", {"zoom_in": 1, "zoom_out": 2, "move_left": 3, "move_right": 4, "move_up": 5, "move_down": 6, "reset": 7}, 6),
}

PROMPT = (
    "포토존 요청을 기능 하나로 바꾸고 함수 호출만 출력하세요. "
    "0 배치(layout=1 한 장,2 네 컷); 1 프레임(frame=1 기본,2 별빛,3 파티,4 별곰이); "
    "2 타이머(seconds=3,10,15,20); 3 촬영(); "
    "4 실행 보류(reason=1 불명확,2 미지원,3 복합); "
    "5 촬영 화면(view=1 네 칸,2 전체); "
    "6 구도(adjustment=1 확대,2 축소,3 왼쪽,4 오른쪽,5 위,6 아래,7 초기화).\n"
    "요청: {utterance}\n출력:"
)


def completion(row: dict) -> str:
    action = row["action"]
    if action == "capture_photo":
        if row["args"]:
            raise ValueError(f"unexpected capture arguments: {row['id']}")
        return "<maum_3>()<maum_end>"
    key, values, token = VALUES[action]
    if set(row["args"]) != {key}:
        raise ValueError(f"invalid arguments: {row['id']}")
    value = values[row["args"][key]]
    return f"<maum_{token}>({key}={value})<maum_end>"


def export(source: Path = SOURCE, destination: Path = DESTINATION) -> Counter:
    rows = json.loads(source.read_text(encoding="utf-8"))
    if len({row["utterance"] for row in rows}) != len(rows):
        raise ValueError("duplicate utterances across splits")
    destination.mkdir(parents=True, exist_ok=True)
    counts: Counter = Counter()
    for split, name in (("train", "train.jsonl"), ("eval", "valid.jsonl"), ("test", "test.jsonl")):
        with (destination / name).open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                if row["split"] != split:
                    continue
                example = {"prompt": PROMPT.format(utterance=row["utterance"]), "completion": completion(row)}
                stream.write(json.dumps(example, ensure_ascii=False) + "\n")
                counts[split] += 1
    if counts != {"train": 1280, "eval": 256, "test": 256}:
        raise ValueError(f"unexpected split sizes: {counts}")
    (destination / "special_tokens.json").write_text(
        json.dumps({"additional_special_tokens": [*(f"<maum_{i}>" for i in range(7)), "<maum_end>"]}, indent=2) + "\n"
    )
    return counts


if __name__ == "__main__":
    print(export())
