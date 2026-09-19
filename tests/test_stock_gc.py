import json
from pathlib import Path
import tempfile
import unittest

from stock_gc import Adapter, TOOL, checkpoint, checkpoint_text


CP = {'completed_phase': 'verified phase', 'next_focus': 'continue task',
      'keep': ['objective'], 'verification': ['proof']}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server, self.client, self.reports = [], [], []
        self.a = Adapter(self.server.append, self.client.append, self.tmp.name,
                         self.reports.append, '/tmp/test-gc.sock')
        self.a.hook_trust = {'stop': {}, 'post': {}}
        self.a.owned.add('thread')

    def response(self, result=None, error=None):
        msg = {'id': self.server[-1]['id']}
        msg['error' if error else 'result'] = error or result or {}
        self.a.from_server(msg)

    def request_checkpoint(self, cp=None):
        self.a.from_server({'id': 'tool-call', 'method': 'item/tool/call', 'params': {
            'threadId': 'thread', 'turnId': 'phase', 'tool': TOOL['name'],
            'arguments': CP if cp is None else cp}})
        self.assertTrue(self.server[-1]['result']['success'])

    def start_compaction(self, active_goal=False):
        self.request_checkpoint()
        self.replies = []
        self.a.from_hook({'session_id': 'thread', 'turn_id': 'phase',
                          'hook_event_name': 'Stop'}, self.replies.append)
        self.assertEqual(self.server[-1]['method'], 'thread/goal/get')
        self.response({'goal': {'status': 'active'}} if active_goal else {})
        self.assertEqual(self.server[-1]['method'], 'thread/compact/start')
        self.response()
        self.a.from_server({'method': 'turn/completed', 'params': {'threadId': 'thread',
            'turn': {'id': 'phase', 'status': 'interrupted'}}})
        self.a.from_server({'method': 'turn/started', 'params': {'threadId': 'thread',
            'turn': {'id': 'compact'}}})

    def finish_compaction(self):
        self.a.from_hook({'session_id': 'thread', 'turn_id': 'compact',
                          'hook_event_name': 'PostCompact'}, self.replies.append)
        self.assertEqual(self.server[-1]['method'], 'thread/inject_items')
        self.assertEqual(self.replies, [True])  # PostCompact held until persistence.
        injected = self.server[-1]['params']['items'][0]['content'][0]['text']
        self.assertIn(checkpoint_text(checkpoint(CP)), injected)
        self.response()
        self.assertEqual(self.replies, [True, True])
        self.a.from_server({'method': 'turn/completed', 'params': {'threadId': 'thread',
            'turn': {'id': 'compact', 'status': 'completed'}}})

    def test_complete_task_handoff(self):
        self.start_compaction()
        self.finish_compaction()
        self.assertEqual(self.server[-1]['method'], 'turn/start')
        self.response()
        self.assertFalse(self.a.pending)

    def test_active_goal_owns_continuation(self):
        self.start_compaction(active_goal=True)
        self.finish_compaction()
        self.assertEqual(self.server[-1]['method'], 'thread/inject_items')
        self.assertFalse(self.a.pending)

    def test_large_checkpoint_is_saved_and_injected_without_truncation(self):
        cp = dict(CP, keep=['é🚀' * 100000] * 3)
        self.request_checkpoint(cp)
        saved = list(Path(self.tmp.name).glob('*.json'))
        self.assertEqual(json.loads(saved[0].read_text())['checkpoint']['keep'], cp['keep'])
        self.assertEqual(saved[0].stat().st_mode & 0o777, 0o600)
        self.assertIn(json.dumps(cp['keep'], ensure_ascii=False), checkpoint_text(checkpoint(cp)))

    def test_user_steering_cancels_before_compact(self):
        self.request_checkpoint()
        replies = []
        self.a.from_hook({'session_id': 'thread', 'turn_id': 'phase',
                          'hook_event_name': 'Stop'}, replies.append)
        old_id = self.server[-1]['id']
        msg = {'id': 'steer', 'method': 'turn/steer', 'params': {'threadId': 'thread'}}
        self.a.from_client(msg)
        self.a.from_server({'id': old_id, 'result': {}})
        self.assertEqual(self.server[-1], msg)
        self.assertEqual(replies, [True])
        self.assertFalse(self.a.pending)

    def test_late_old_response_cannot_advance_new_checkpoint(self):
        self.start_compaction()
        self.a.from_hook({'session_id': 'thread', 'turn_id': 'compact',
                          'hook_event_name': 'PostCompact'}, self.replies.append)
        old_id = self.server[-1]['id']
        self.a.cancel('thread')
        self.request_checkpoint()
        state = self.a.pending['thread']
        self.a.from_server({'id': old_id, 'result': {}})
        self.assertEqual(state['phase'], 'waiting')

    def test_injection_failure_does_not_start_manual_continuation(self):
        self.start_compaction()
        self.a.from_hook({'session_id': 'thread', 'turn_id': 'compact',
                          'hook_event_name': 'PostCompact'}, self.replies.append)
        self.response(error={'message': 'failure'})
        self.assertEqual(self.replies, [True, False])
        self.assertFalse(self.a.pending)
        self.assertEqual(self.server[-1]['method'], 'thread/inject_items')
        self.assertTrue(list(Path(self.tmp.name).glob('*.json')))

    def test_caller_tool_collision_passes_through(self):
        self.a.from_client({'id': 2, 'method': 'thread/start', 'params': {'dynamicTools': [TOOL]}})
        self.assertNotIn(2, self.a.starts)
        msg = {'id': 'foreign', 'method': 'item/tool/call', 'params': {
            'threadId': 'other', 'tool': 'compact_context', 'arguments': CP}}
        self.a.from_server(msg)
        self.assertEqual(self.client[-1], msg)

    def test_empty_start_params_receive_tool_and_hook_config(self):
        request = {'id': 'empty', 'method': 'thread/start', 'params': {}}
        self.a.from_client(request)
        self.assertEqual(self.server[-1]['params']['dynamicTools'], [TOOL])
        self.assertTrue(self.server[-1]['params']['config']['features.hooks'])

    def test_missing_hooks_omit_tool(self):
        self.a.hook_trust = {}
        self.a.from_client({'id': 2, 'method': 'thread/start', 'params': {}})
        self.assertNotIn('dynamicTools', self.server[-1]['params'])

    def test_other_requests_and_approvals_pass_unchanged(self):
        msg = {'id': 5, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': 'thread'}}
        self.a.from_server(msg)
        self.assertEqual(self.client[-1], msg)

    def test_six_handoffs_have_no_request_cap(self):
        for _ in range(6):
            self.start_compaction()
            self.finish_compaction()
            self.response()
        self.assertEqual(len(list(Path(self.tmp.name).glob('*.json'))), 6)


if __name__ == '__main__':
    unittest.main()
