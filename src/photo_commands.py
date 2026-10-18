"""Strict photo-booth model output contract. This module never executes actions."""
import re

PROMPT = (
    "포토존 요청을 기능 하나로 바꾸고 함수 호출만 출력하세요. "
    "0 배치(layout=1 한 장,2 네 컷); 1 프레임(frame=1 기본,2 별빛,3 파티,4 별곰이); "
    "2 타이머(seconds=3,10,15,20); 3 촬영(); "
    "4 실행 보류(reason=1 불명확,2 미지원,3 복합); "
    "5 촬영 화면(view=1 네 칸,2 전체); "
    "6 구도(adjustment=1 확대,2 축소,3 왼쪽,4 오른쪽,5 위,6 아래,7 초기화).\n"
    "요청: {utterance}\n출력:"
)

# Numeric values are the training contract, not arbitrary Python expressions.
FUNCTIONS = {
    0: ("select_layout", "layout", {1: "single", 2: "four_cut"}),
    1: ("set_frame", "frame", {1: "plain", 2: "starlight", 3: "party", 4: "buddy"}),
    2: ("set_timer", "seconds", {3: 3, 10: 10, 15: 15, 20: 20}),
    3: ("capture_photo", None, {}),
    4: ("no_action", "reason", {1: "ambiguous", 2: "unsupported", 3: "multi_action"}),
    5: ("set_shooting_view", "view", {1: "strip", 2: "full"}),
    6: ("adjust_view", "adjustment", {1: "zoom_in", 2: "zoom_out", 3: "move_left",
        4: "move_right", 5: "move_up", 6: "move_down", 7: "reset"}),
}


def parse_command(raw):
    """Accept exactly one allowed function and its exact allowed argument."""
    if not isinstance(raw, str):
        raise ValueError("model output must be text")
    match = re.fullmatch(r"<maum_([0-6])>\(([^()]*)\)<maum_end>", raw.strip())
    if not match:
        raise ValueError("invalid function-call format")
    action, key, values = FUNCTIONS[int(match[1])]
    argument = match[2]
    if key is None:
        if argument:
            raise ValueError("capture_photo accepts no arguments")
        args = {}
    else:
        value_match = re.fullmatch(re.escape(key) + r"=([0-9]+)", argument)
        if not value_match or int(value_match[1]) not in values:
            raise ValueError("invalid function argument")
        args = {key: values[int(value_match[1])]}
    return {"action": action, "args": args, "raw_output": raw.strip(),
            "confirmation_required": action == "capture_photo"}


class PhotoCommandModel:
    """Load once; translate STT text using the trained LoRA and added tokens."""

    def __init__(self, model, adapter):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from pathlib import Path

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.float16 if self.device != "cpu" else torch.float32
        adapter = Path(adapter)
        import json
        metadata_file = adapter / "MODEL_INFO.json"
        revision = None
        if metadata_file.exists():
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            if metadata.get("base_model") == model:
                revision = metadata.get("base_revision")
        self.tokenizer = AutoTokenizer.from_pretrained(adapter / "tokenizer", local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(model, revision=revision, dtype=dtype, local_files_only=True)
        base.resize_token_embeddings(len(self.tokenizer), mean_resizing=False)
        self.model = PeftModel.from_pretrained(base, adapter / "adapter", local_files_only=True).to(self.device).eval()
        self.end_id = self.tokenizer.convert_tokens_to_ids("<maum_end>")
        if self.tokenizer.encode("<maum_end>", add_special_tokens=False) != [self.end_id] or self.end_id == self.tokenizer.unk_token_id:
            raise ValueError("trained function tokenizer is required")

    def interpret(self, text):
        import time
        if not isinstance(text, str) or not text.strip() or len(text) > 512:
            raise ValueError("text must contain 1 to 512 characters")
        started = time.perf_counter()
        inputs = self.tokenizer(PROMPT.format(utterance=text.strip()), return_tensors="pt").to(self.device)
        if inputs["input_ids"].shape[1] > 512:
            raise ValueError("input exceeds the 512-token training limit")
        with self.torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=32, do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=[self.tokenizer.eos_token_id, self.end_id])
        raw = self.tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=False)
        result = parse_command(raw)
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result
