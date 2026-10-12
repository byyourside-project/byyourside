"""Run the rehearsal and live apps with independent APIs and an explicit handoff."""
import json
from http.server import ThreadingHTTPServer

from src.presentation_server import PresentationApp, make_server
from src.review_server import ReviewApp, make_handler


def create_servers(root, review_port=8765, live_port=8766, coach=None):
    deck = json.loads((root / 'examples/presentation_script_deck.json').read_text(encoding='utf-8'))
    live = PresentationApp(deck, coach=coach, output_dir=str(root / 'logs/presentation_sessions'))
    servers = []
    try:
        live_server = make_server(live, live_port)
        servers.append(live_server)
        live_url = f'http://127.0.0.1:{live_server.server_port}'
        review = ReviewApp(root, presentation=live, presentation_url=live_url)
        review_server = ThreadingHTTPServer(('127.0.0.1', review_port), make_handler(review))
        servers.append(review_server)
        live.review_url = f'http://127.0.0.1:{review_server.server_port}'
        return live, review_server, live_server
    except BaseException:
        for server in servers:
            server.server_close()
        live.close()
        raise
