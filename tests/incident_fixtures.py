"""Synthetic incident/provider fixtures. No Docker or real AI account."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

IDENTIFIER = 'a' * 64
NOW = 1800000000
SECRET = 'never-export-this-fixture-secret'


def status():
    return dict(api_version=1, ready=True, last_cycle_success=True, last_success_at=NOW - 2,
                age_seconds=2, containers=[dict(id=IDENTIFIER, name=SECRET, status='running',
                    health='unhealthy', mem_percent=96, mem_usage=960, mem_limit=1000,
                    oom_events=2, starts_recent=4, event_history_complete=1,
                    event_window_end=NOW - 2, event_window_seconds=300,
                    restart_attempts=3, restart_limit=3, manual_actions_protected=True,
                    maintenance_active=True, maintenance_until=NOW + 300)])


def analysis():
    return dict(hypotheses=[dict(text='Memory pressure is a possible cause, not a proven diagnosis.', evidence=['e1'])],
                missing_evidence=['A single sample cannot establish a trend.'],
                suggested_checks=[dict(text='Review the recent memory trend without changing the container.', evidence=['e1'])])


def completion(value=None, provider='openai-compatible'):
    message = dict(role='assistant', content=json.dumps(analysis() if value is None else value))
    if provider == 'ollama':
        return dict(done=True, done_reason='stop', message=message, prompt_eval_count=200, eval_count=60)
    if provider == 'anthropic':
        return dict(type='message', role='assistant', stop_reason='end_turn',
                    content=[dict(type='text', text=message['content'])],
                    usage=dict(input_tokens=200, output_tokens=60))
    return dict(choices=[dict(finish_reason='stop', message=message)],
                usage=dict(prompt_tokens=200, completion_tokens=60, total_tokens=260))


@contextmanager
def provider_server(responder):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond(None)

        def do_POST(self):
            self.respond(json.loads(self.rfile.read(int(self.headers['Content-Length']))))

        def respond(self, payload):
            requests.append(dict(path=self.path, payload=payload, authorization=self.headers.get('Authorization'),
                                 anthropic_version=self.headers.get('anthropic-version')))
            try:
                responder(self, payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%d' % server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def respond_json(handler, value, code=200):
    data = json.dumps(value).encode()
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
