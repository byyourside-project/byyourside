"""Run the standalone photo booth demo at http://127.0.0.1:8770/."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import json
import sys


ROOT = Path(__file__).resolve().parents[1] / "web" / "photo_booth"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--model", default="meta-llama/Llama-3.2-1B-Instruct")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--model-adapter", type=Path, help="Trained folder containing adapter/ and tokenizer/")
    mode.add_argument("--trained", action="store_true", help="Use the portable trained adapter included on this branch")
    args = parser.parse_args()
    if args.trained:
        args.model_adapter = ROOT.parents[1] / "artifacts/photo_booth_llama_v09"
    if args.model_adapter:
        sys.path.insert(0, str(ROOT.parents[1]))
        from src.photo_commands import PhotoCommandModel
        from scripts.run_photo_model import create_server
        interpreter = PhotoCommandModel(args.model, args.model_adapter)
        server = create_server(interpreter, args.port,
            [f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"], static_directory=ROOT)
    else:
        class RuleHandler(SimpleHTTPRequestHandler):
            extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

            def do_GET(self):
                if self.path == "/api/photo/config":
                    body = json.dumps({"ready": True, "backend": "rules"}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()
        handler = partial(RuleHandler, directory=str(ROOT))
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"사진관 시연: http://127.0.0.1:{server.server_port}/", flush=True)
    print("명령 해석: " + ("학습 모델 (화면에서 기존 키워드로 전환 가능)" if args.model_adapter else "기존 키워드"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
