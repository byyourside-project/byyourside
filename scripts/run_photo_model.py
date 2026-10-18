"""Local trained-model API for an existing photo-booth STT frontend."""
import argparse
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.photo_commands import PhotoCommandModel


def create_server(interpreter, port=8771, allowed_origins=(), static_directory=None):
    lock = threading.Lock()
    origins = set(allowed_origins)

    class Handler(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_directory) if static_directory else None, **kwargs)

        def log_message(self, *args):
            pass  # Do not log transcripts or request bodies.

        def origin_allowed(self):
            return self.headers.get("Origin") in origins or not self.headers.get("Origin")

        def reply(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            origin = self.headers.get("Origin")
            if origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.origin_allowed():
                return self.reply(403, {"error": "origin_not_allowed"})
            if self.path not in ("/health", "/api/photo/config"):
                if static_directory and not self.path.startswith("/api/"):
                    return super().do_GET()
                return self.reply(404, {"error": "not_found"})
            self.reply(200, {"ready": True, "backend": "trained_llama", "device": interpreter.device})

        def do_OPTIONS(self):
            if not self.origin_allowed():
                return self.reply(403, {"error": "origin_not_allowed"})
            self.send_response(204)
            origin = self.headers.get("Origin")
            if origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            if not self.origin_allowed():
                return self.reply(403, {"error": "origin_not_allowed"})
            if self.path != "/api/photo/interpret":
                return self.reply(404, {"error": "not_found"})
            if self.headers.get_content_type() != "application/json":
                return self.reply(415, {"error": "json_required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    return self.reply(413, {"error": "invalid_body_size"})
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(length))
                text = payload.get("text") if isinstance(payload, dict) else None
                if not isinstance(text, str) or not text.strip() or len(text) > 512:
                    raise ValueError("invalid text")
            except (ValueError, OSError):
                return self.reply(400, {"error": "invalid_request"})
            if not lock.acquire(blocking=False):
                return self.reply(503, {"error": "model_busy"})
            try:
                self.reply(200, interpreter.interpret(text))
            except ValueError:
                self.reply(422, {"error": "invalid_input_or_model_output"})
            except Exception:
                self.reply(500, {"error": "model_inference_failed"})
            finally:
                lock.release()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="meta-llama/Llama-3.2-1B-Instruct")
    parser.add_argument("--adapter", type=Path, required=True, help="Folder containing adapter/ and tokenizer/")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--origin", action="append", default=[], help="Exact frontend origin allowed to call this API")
    parser.add_argument("--text", help="Interpret one text without starting a server")
    args = parser.parse_args()
    interpreter = PhotoCommandModel(args.model, args.adapter)
    if args.text is not None:
        print(json.dumps(interpreter.interpret(args.text), ensure_ascii=False))
        return
    server = create_server(interpreter, args.port, args.origin)
    print(f"Photo model ready: http://127.0.0.1:{server.server_port} ({interpreter.device})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
