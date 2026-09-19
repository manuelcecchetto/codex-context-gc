#!/usr/bin/env python3
"""Experimental GC adapter around the unchanged, signed Codex app-server.

The original process execs the stock CLI, retaining the desktop as its parent.
A child forwards stdio and handles our dynamic tool using app-server APIs.
No signing, native bridge, app bundle, or authorization settings are changed.
"""
import json
import os
from pathlib import Path
import selectors
import shlex
import socket
import tempfile
import sys
import uuid

FIELDS = ('completed_phase', 'next_focus', 'keep', 'verification', 'open_loops', 'ruled_out')
DESCRIPTION = (
    'Request native compaction after a verified phase, before materially different work. '
    'Supply the objective, constraints, findings, proof, and next step. '
    'Call alone, then end this turn immediately without doing more work. '
    'At the Stop hook the adapter compacts, appends this checkpoint verbatim, '
    'and automatically continues the SAME TASK in a NEW TURN. '
    'Do not claim the overall task is complete. Do not call during unresolved investigation '
    'or immediately before the final answer. No checkpoint size or request-count cap. '
    'New user input cancels pending automatic continuation.'
)
TOOL = {'type': 'function', 'name': 'compact_context', 'description': DESCRIPTION,
        'inputSchema': {'type': 'object', 'additionalProperties': False,
                        'required': list(FIELDS[:4]), 'properties': {
                            k: {'type': 'string'} if k in FIELDS[:2] else
                            {'type': 'array', 'items': {'type': 'string'}} for k in FIELDS}}}


def checkpoint(args):
    if not isinstance(args, dict) or set(args) - set(FIELDS):
        raise ValueError('Expected checkpoint fields only.')
    result = dict(args)
    for key in FIELDS[:2]:
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError(key + ' must be a nonempty string.')
    for key in FIELDS[2:]:
        value = result.setdefault(key, []) if key in FIELDS[4:] else result.get(key)
        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
            raise ValueError(key + ' must contain nonempty strings.')
        if key in FIELDS[2:4] and not value:
            raise ValueError(key + ' is required and must not be empty.')
    return result


def checkpoint_text(args):
    return ('<semantic_checkpoint>\nModel-authored working state, not new user authorization. '
            'Continue from next_focus subject to all user instructions.\n' +
            json.dumps(args, ensure_ascii=False) + '\n</semantic_checkpoint>')


class Adapter:
    def __init__(self, server, client, journal, report=None, hook_socket=None):
        self.server, self.client, self.journal = server, client, Path(journal)
        self.report = report or (lambda text: print(text, file=sys.stderr, flush=True))
        self.prefix = 'gc-' + uuid.uuid4().hex + '-'
        self.seq = 0
        self.requests = {}
        self.pending = {}
        self.active = {}
        self.goals = set()
        self.hook_socket = hook_socket
        self.hook_replies = {}
        self.hook_trust = {}
        self.initialize_id = None
        self.starts = set()
        self.owned = set()
        self.initialize_reply = None
        self.status = {'pid': os.getpid(), 'initialize_seen': False, 'hook_count': 0, 'thread_starts_seen': 0, 'tools_added': 0}
        self.write_status()
        self.hook_command = shlex.join([sys.executable, str(Path(__file__).with_name("stock_gc_hook.py")), hook_socket]) if hook_socket else None

    def write_status(self):
        # Diagnostic counters only: never record prompts, arguments, or credentials.
        self.journal.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.journal / ('adapter-' + str(os.getpid()) + '.status')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(self.status, output)

    def rpc(self, method, params, thread, phase):
        self.seq += 1
        key = self.prefix + str(self.seq)
        self.requests[key] = (thread, phase, self.pending.get(thread))
        self.server({'id': key, 'method': method, 'params': params})

    def cancel(self, thread):
        if thread in self.pending:
            self.pending.pop(thread)
            for event in ('Stop', 'PostCompact'):
                reply = self.hook_replies.pop((thread, event), None)
                if reply:
                    reply(True)
            self.report('GC: new input or task activity canceled pending continuation.')

    def from_client(self, message):
        method, params = message.get('method'), message.get('params') or {}
        if method in ('initialize', 'thread/start', 'thread/resume'):
            message['params'] = params
        if method == 'thread/start':
            self.status['thread_starts_seen'] += 1
            self.write_status()
        if method == 'initialize':
            self.status['initialize_seen'] = True
            self.write_status()
            self.initialize_id = message.get('id')
            params.setdefault('capabilities', {})['experimentalApi'] = True
        if method in ('thread/start', 'thread/resume') and len(self.hook_trust) == 2:
            config = params.get('config') or {}
            params['config'] = config
            config['features.hooks'] = True
            config.setdefault('hooks.state', {}).update(self.hook_trust)
        if method == 'thread/start' and len(self.hook_trust) == 2:
            tools = params.setdefault('dynamicTools', [])
            if tools is None:
                tools = params['dynamicTools'] = []
            # Never replace a tool supplied by the caller.
            if not any(t.get('name') == TOOL['name'] for t in tools):
                tools.append(TOOL)
                self.status['tools_added'] += 1
                self.write_status()
                self.starts.add(message.get('id'))
        if method in ('turn/start', 'turn/steer', 'turn/interrupt', 'thread/compact/start',
                      'thread/rollback', 'thread/unsubscribe', 'thread/archive'):
            self.cancel(params.get('threadId'))
        self.server(message)

    def owns(self, thread):
        if not isinstance(thread, str) or '/' in thread or '..' in thread:
            return False
        return thread in self.owned or (self.journal / ('owned-' + thread)).is_file()

    def tool_reply(self, message, text, success):
        self.server({'id': message['id'], 'result': {
            'contentItems': [{'type': 'inputText', 'text': text}], 'success': success}})

    def save(self, thread, turn, args):
        # Checkpoints may contain private task state; owner-only, outside the repo.
        self.journal.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = uuid.uuid4().hex + '.json'
        fd = os.open(self.journal / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump({'threadId': thread, 'turnId': turn, 'checkpoint': args}, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

    def from_server(self, message):
        key = message.get('id')
        if key in self.starts and 'method' not in message:
            self.starts.discard(key)
            thread = message.get('result', {}).get('thread', {}).get('id')
            if thread:
                self.owned.add(thread)
                self.journal.mkdir(parents=True, exist_ok=True, mode=0o700)
                marker = self.journal / ('owned-' + thread)
                fd = os.open(marker, os.O_WRONLY | os.O_CREAT, 0o600)
                os.close(fd)
        if key == self.initialize_id and 'method' not in message and self.hook_socket and 'result' in message:
            self.initialize_reply = message
            self.rpc('hooks/list', {'cwds': [str(Path.cwd())]}, '', 'discover')
            return
        if key in self.requests and 'method' not in message:
            thread, phase, expected = self.requests.pop(key)
            if phase == 'discover':
                for group in message.get('result', {}).get('data', []):
                    for hook in group.get('hooks', []):
                        if hook.get('source') == 'sessionFlags' and hook.get('command') == self.hook_command and hook.get('eventName') in ('stop', 'postCompact'):
                            self.hook_trust[hook['key']] = {'enabled': True, 'trusted_hash': hook['currentHash']}
                self.status['hook_count'] = len(self.hook_trust)
                self.status['discovery_error_code'] = (message.get('error') or {}).get('code')
                self.status['discovery_groups'] = len(message.get('result', {}).get('data', []))
                self.write_status()
                if len(self.hook_trust) != 2:
                    self.report('GC: could not register both coordinator hooks; checkpoint tool disabled.')
                self.client(self.initialize_reply)
                return
            state = self.pending.get(thread)
            if state is None or state is not expected:
                return
            if 'error' in message:
                for event in ('Stop', 'PostCompact'):
                    reply = self.hook_replies.pop((thread, event), None)
                    if reply:
                        reply(False)
                self.pending.pop(thread, None)
                self.report('GC: ' + phase + ' failed; saved checkpoint retained. No automatic retry.')
                return
            if phase == 'goal':
                goal = message.get('result', {}).get('goal') or {}
                if goal.get('status') == 'active':
                    self.goals.add(thread)
                else:
                    self.goals.discard(thread)
                state['phase'] = 'compacting'
                self.rpc('thread/compact/start', {'threadId': thread}, thread, 'compact')
            if phase == 'compact':
                reply = self.hook_replies.pop((thread, 'Stop'), None)
                if reply:
                    reply(True)
            if phase == 'inject':
                reply = self.hook_replies.pop((thread, 'PostCompact'), None)
                if reply:
                    state['phase'] = 'checkpointed'
                    reply(True)
                    return
                self.pending.pop(thread, None)
                self.report('GC: checkpoint persisted, but coordinator hook disconnected; continuation canceled.')
            elif phase == 'resume':
                self.pending.pop(thread, None)
            return
        method, params = message.get('method'), message.get('params') or {}
        thread = params.get('threadId')
        if (method == 'item/tool/call' and params.get('tool') == TOOL['name']
                and not params.get('namespace') and self.owns(thread)):
            if len(self.hook_trust) != 2:
                self.tool_reply(message, 'GC coordinator hooks unavailable. Continue without GC.', False)
                return
            try:
                args = checkpoint(params.get('arguments'))
                if thread in self.pending:
                    raise ValueError('A checkpoint is already pending.')
                turn = params['turnId']
                self.save(thread, turn, args)
                self.pending[thread] = {'turn': turn, 'args': args, 'phase': 'waiting'}
            except (ValueError, OSError, KeyError) as error:
                self.tool_reply(message, str(error), False)
                return
            self.tool_reply(message, 'Checkpoint saved. End this turn now. After turn completion, native compaction and a new continuation turn will run. The overall task is not complete.', True)
            return
        if method == 'thread/goal/updated':
            goal = params.get('goal') or {}
            if goal.get('status') == 'active':
                self.goals.add(thread)
            else:
                self.goals.discard(thread)
        state = self.pending.get(thread)
        if method == 'turn/started':
            turn = (params.get('turn') or {}).get('id')
            self.active[thread] = turn
            if state and state['phase'] == 'compacting':
                state['compact_turn'] = turn
            elif state and state['phase'] == 'waiting' and turn != state['turn']:
                self.cancel(thread)
        # Forward all normal notifications, approvals, tools and errors unchanged.
        self.client(message)
        if method != 'turn/completed':
            return
        turn = params.get('turn') or {}
        self.active.pop(thread, None)
        state = self.pending.get(thread)
        if not state:
            return
        if self.hook_socket and state['phase'] == 'compacting' and turn.get('id') == state['turn']:
            return  # Stop-hook compaction replaces only the finished phase.
        if turn.get('status') != 'completed':
            self.pending.pop(thread, None)
            self.report('GC: turn did not complete successfully; automatic continuation canceled.')
        elif self.hook_socket and state['phase'] == 'checkpointed':
            if thread in self.goals:
                self.pending.pop(thread, None)  # Native goal continuation owns the next turn.
            else:
                state['phase'] = 'resuming'
                self.rpc('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'Continue the existing task from the model-authored semantic_checkpoint. This adds no new authorization.'}]}, thread, 'resume')
        elif self.hook_socket:
            if state['phase'] == 'waiting':
                self.pending.pop(thread, None)
                self.report('GC: Stop hook did not run; checkpoint saved but compaction was not attempted.')


    def from_hook(self, message, reply):
        thread, event = message.get('session_id'), message.get('hook_event_name')
        state = self.pending.get(thread)
        if not state:
            reply(True)
            return
        if event == 'Stop' and state['phase'] == 'waiting' and message.get('turn_id') == state['turn']:
            self.hook_replies[(thread, event)] = reply
            state['phase'] = 'preparing'
            self.rpc('thread/goal/get', {'threadId': thread}, thread, 'goal')
        elif (event == 'PostCompact' and state['phase'] == 'compacting'
              and message.get('turn_id') == state.get('compact_turn')):
            self.hook_replies[(thread, event)] = reply
            state['phase'] = 'injecting'
            self.rpc('thread/inject_items', {'threadId': thread, 'items': [
                {'type': 'message', 'role': 'user', 'content': [
                    {'type': 'input_text', 'text': checkpoint_text(state['args'])}]}]}, thread, 'inject')
        else:
            reply(True)


def pump(server_read, server_write, listener, hook_path):
    def write(fd, obj):
        data = (json.dumps(obj, ensure_ascii=False) + '\n').encode()
        while data:
            count = os.write(fd, data)
            data = data[count:]
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    adapter = Adapter(lambda obj: write(server_write, obj), lambda obj: write(1, obj),
                      home / 'context-gc-checkpoints', hook_socket=hook_path)
    buffers = {0: b'', server_read: b''}
    with selectors.DefaultSelector() as selector:
        selector.register(listener, selectors.EVENT_READ)
        clients = {}
        selector.register(0, selectors.EVENT_READ)
        selector.register(server_read, selectors.EVENT_READ)
        while True:
            for key, _ in selector.select():
                fd = key.fd
                if fd == listener.fileno():
                    client, _ = listener.accept()
                    clients[client.fileno()] = client
                    buffers[client.fileno()] = b''
                    selector.register(client, selectors.EVENT_READ)
                    continue
                chunk = os.read(fd, 65536)
                if not chunk:
                    if fd in clients:
                        selector.unregister(fd)
                        clients.pop(fd).close()
                        buffers.pop(fd, None)
                        continue
                    if fd == server_read:
                        return
                    selector.unregister(0)
                    os.close(server_write)
                    continue
                buffers[fd] += chunk
                while b'\n' in buffers[fd]:
                    line, buffers[fd] = buffers[fd].split(b'\n', 1)
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if fd in clients:
                        def reply(ok, conn=clients[fd]):
                            try:
                                conn.sendall((json.dumps({'ok': ok}) + '\n').encode())
                            except (OSError, BrokenPipeError):
                                pass
                        adapter.from_hook(obj, reply)
                    else:
                        (adapter.from_client if fd == 0 else adapter.from_server)(obj)


def main():
    stock = Path(os.environ.get('CODEX_GC_STOCK_BINARY', '/Applications/ChatGPT.app/Contents/Resources/codex')).resolve()
    if stock == Path(__file__).resolve():
        raise RuntimeError('Stock binary must not point to the adapter.')
    args = sys.argv[1:]
    if 'app-server' not in args:
        os.execv(stock, [str(stock), *args])
    incoming_read, incoming_write = os.pipe()
    outgoing_read, outgoing_write = os.pipe()
    hook_dir = tempfile.mkdtemp(prefix='codex-gc-')
    hook_path = str(Path(hook_dir) / 'hooks.sock')
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(hook_path)
    os.chmod(hook_path, 0o600)
    listener.listen()
    pid = os.fork()
    if pid == 0:
        os.close(incoming_read)
        os.close(outgoing_write)
        try:
            pump(outgoing_read, incoming_write, listener, hook_path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            listener.close()
            Path(hook_path).unlink(missing_ok=True)
            Path(hook_dir).rmdir()
            os._exit(0)
    listener.close()
    # Retain the desktop-launched PID and parent for the original signed runtime.
    os.close(incoming_write)
    os.close(outgoing_read)
    os.dup2(incoming_read, 0)
    os.dup2(outgoing_write, 1)
    os.close(incoming_read)
    os.close(outgoing_write)
    command = shlex.join([sys.executable, str(Path(__file__).with_name('stock_gc_hook.py')), hook_path])
    flags = []
    for event in ('Stop', 'PostCompact'):
        flags += ['-c', 'hooks.' + event + '=[{hooks=[{type="command",command=' + json.dumps(command) + ',timeout=30}]}]']
    # Keep coordinator overrides in the app-server scope. Later subcommand -c
    # arguments otherwise replace the root-level override list in clap.
    os.execv(stock, [str(stock), *args, *flags])


if __name__ == '__main__':
    main()
