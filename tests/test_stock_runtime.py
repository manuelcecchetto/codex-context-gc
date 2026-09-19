"""Opt-in real-runtime integration; all model responses come from localhost.

CODEX_GC_TEST_STOCK=/Applications/ChatGPT.app/Contents/Resources/codex \
  python3 -m unittest discover -s tests -p test_stock_runtime.py -v
"""
import http.server
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
STOCK = os.environ.get('CODEX_GC_TEST_STOCK')
CP = {'completed_phase': 'Synthetic phase verified', 'next_focus': 'Continue synthetic task',
      'keep': ['CHECKPOINT_UNICODE_é🚀'], 'verification': ['Synthetic proof']}


def message(text):
    return {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}


def call(name, args):
    return {'type': 'function_call', 'name': name, 'arguments': json.dumps(args)}


@unittest.skipUnless(STOCK, 'Set CODEX_GC_TEST_STOCK to opt into real signed-runtime tests')
class RuntimeTests(unittest.TestCase):
    def exercise(self, goal=False, cycles=1):
        script = [call('create_goal', {'objective': 'Complete synthetic GC test'})] if goal else []
        markers, continuation_indices = [], []
        for i in range(cycles):
            marker = f'CHECKPOINT_UNICODE_é🚀_{i}'
            markers.append(marker)
            script.extend([call('compact_context', dict(CP, keep=[marker])),
                           message('Checkpoint ready.'), message(f'SYNTHETIC_NATIVE_SUMMARY_{i}')])
            continuation_indices.append(len(script))
        if goal:
            script.append(call('update_goal', {'status': 'complete'}))
        script.append(message('SYNTHETIC_TASK_FINISHED'))
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                i = len(requests) - 1
                item = dict(script[i]) if i < len(script) else message('UNEXPECTED_EXTRA_REQUEST')
                item.update({'id': str(i), 'call_id': f'call-{i}'})
                events = [{'type': 'response.output_item.done', 'item': item},
                          {'type': 'response.completed', 'response': {'id': f'r{i}',
                           'usage': {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110}}}]
                payload = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(prefix='gc-integration-') as tmp:
            home = Path(tmp)
            (home / 'config.toml').write_text(f'''model = "gpt-6-astra"
model_provider = "fixture"
model_auto_compact_token_limit = 1000000
[features]
enable_request_compression = false
[model_providers.fixture]
name = "Fixture"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
supports_websockets = false
requires_openai_auth = false
''')
            env = os.environ.copy()
            env.update(CODEX_HOME=tmp, CODEX_GC_STOCK_BINARY=STOCK)
            env.pop('OPENAI_API_KEY', None)
            with tempfile.TemporaryFile() as errors:
                proc = subprocess.Popen([sys.executable, str(ROOT / 'stock_gc.py'), 'app-server',
                                         '--listen', 'stdio://'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=errors, env=env)
                def send(i, method, params):
                    proc.stdin.write((json.dumps({'id': i, 'method': method, 'params': params}) + '\n').encode())
                    proc.stdin.flush()
                send(1, 'initialize', {'clientInfo': {'name': 'gc_fixture', 'version': '1'},
                                      'capabilities': {'experimentalApi': True}})
                finished, done, goal_complete = False, False, False
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(proc.stdout, selectors.EVENT_READ)
                        deadline, buffer = time.monotonic() + 60, b''
                        while time.monotonic() < deadline and not done:
                            if not selector.select(.2):
                                continue
                            chunk = os.read(proc.stdout.fileno(), 65536)
                            if not chunk:
                                break
                            buffer += chunk
                            while b'\n' in buffer:
                                line, buffer = buffer.split(b'\n', 1)
                                obj = json.loads(line)
                                if obj.get('id') == 1:
                                    # exec preserves the original PID; assert actual process image.
                                    image = subprocess.check_output(['ps', '-p', str(proc.pid), '-o', 'comm='], text=True).strip()
                                    self.assertEqual(Path(image).resolve(), Path(STOCK).resolve())
                                    send(2, 'thread/start', {'cwd': tmp, 'approvalPolicy': 'never', 'sandbox': 'read-only'})
                                elif obj.get('id') == 2:
                                    self.assertNotIn('error', obj)
                                    send(3, 'turn/start', {'threadId': obj['result']['thread']['id'],
                                         'input': [{'type': 'text', 'text': 'Run synthetic GC test.'}]})
                                if obj.get('method') == 'thread/goal/updated':
                                    goal_complete |= (obj['params'].get('goal') or {}).get('status') == 'complete'
                                finished |= b'SYNTHETIC_TASK_FINISHED' in line
                                if finished and obj.get('method') == 'turn/completed':
                                    self.assertEqual(obj['params']['turn']['status'], 'completed')
                                    done = True
                    errors.seek(0)
                    self.assertTrue(done, errors.read().decode()[-4000:])
                    self.assertEqual(len(requests), len(script))
                    for i, index in enumerate(continuation_indices):
                        body = json.dumps(requests[index], ensure_ascii=False)
                        self.assertIn(markers[i], body)
                        self.assertIn(f'SYNTHETIC_NATIVE_SUMMARY_{i}', body)
                    self.assertEqual(len(list((home / 'context-gc-checkpoints').glob('*.json'))), cycles)
                    if goal:
                        self.assertTrue(goal_complete)
                finally:
                    proc.terminate()
                    proc.wait(timeout=5)
                    proc.stdin.close()
                    proc.stdout.close()

    def test_ordinary_task(self):
        self.exercise()

    def test_goal_with_six_compactions(self):
        self.exercise(goal=True, cycles=6)


if __name__ == '__main__':
    unittest.main()
