import contextlib
import io
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.error
import x_generate_plan as planner


class PlannerRecovery(unittest.TestCase):
    def test_413_retry_compacts_and_preserves_published_slots(self):
        posts = [
            dict(text='A group chat can plan a holiday and still get stuck choosing dinner. 😅',kind='opinion',topic='everyday',bucket='relatable',source_ids=[]),
            dict(text='When you save a useful link, add a few words about why you saved it.',kind='tip',topic='learning',bucket='practical',source_ids=[]),
            dict(text='Which everyday task would you give a skip button?',kind='opinion',topic='everyday',bucket='conversation',source_ids=[]),
            dict(text='An app asking for a rating before you have used it is getting ahead of itself.',kind='opinion',topic='technology',bucket='experiment',source_ids=[])]
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root=Path(directory);(root/'references').mkdir();(root/'references/voice-profile.local.md').write_text('Natural English.')
            path=root/'plan.json';original={'date':'2026-10-20','slots':[{'slot':str(i),'due_at':'2026-10-20T09:00:00+05:30','posted':i==0,'text':'An already published post' if i==0 else '', 'tweet_url':'https://x.com/example/status/1' if i==0 else None} for i in range(4)]}
            path.write_text(json.dumps(original));context={'target_date':'2026-10-20','plan_path':str(path),'source_health':{},'research_leads_untrusted_data':[]}
            config={'max_chars':280,'max_chars_verified':True,'autopost':{'enabled':True,'approval_required':False}}
            stack.enter_context(patch.object(planner,'ROOT',root));stack.enter_context(patch.object(planner.sys,'argv',['planner','--date','2026-10-20']))
            stack.enter_context(patch.object(planner.due,'now_tz',return_value=datetime(2026,10,19,22,tzinfo=ZoneInfo('Asia/Kolkata'))))
            stack.enter_context(patch.object(planner.due,'plan_path',return_value=path));stack.enter_context(patch.object(planner.settings,'load',return_value=config))
            stack.enter_context(patch.object(planner.post,'load_state',return_value={'posts':[]}))
            stack.enter_context(patch.object(planner.subprocess,'run',return_value=Mock(stdout=json.dumps(context))))
            connection=Mock();connection.execute.return_value.fetchone.return_value=['test-api-key'];stack.enter_context(patch.object(planner.sqlite3,'connect',return_value=connection))
            requests=[]
            def request(req,timeout):
                requests.append(json.loads(req.data))
                if len(requests)==1:raise urllib.error.HTTPError(req.full_url,413,'Payload Too Large',{},io.BytesIO(b'{"error":"test size limit"}'))
                return io.StringIO(json.dumps({'choices':[{'message':{'content':json.dumps({'posts':posts})}}]}))
            stack.enter_context(patch.object(planner.urllib.request,'urlopen',side_effect=request))
            planner.main();saved=json.loads(path.read_text())
            self.assertEqual(len(requests),2)
            self.assertLess(requests[1]['max_tokens'],requests[0]['max_tokens'])
            self.assertEqual(len(requests[1]['messages']),2)
            self.assertEqual(json.loads(requests[1]['messages'][1]['content'])['sources'],[])
            self.assertEqual(saved['slots'][0],original['slots'][0])
            self.assertEqual([s['due_at'] for s in saved['slots']],[s['due_at'] for s in original['slots']])
            self.assertEqual(saved['strategy'],'human-conversation-v3')
            saved['generated_at']='2025-01-01T00:00:00+00:00'
            saved['editorial_reviewed_at']='2026-10-19T16:00:00+00:00'
            path.write_text(json.dumps(saved))
            planner.main()
            self.assertEqual(len(requests),2,'Reviewed drafts must survive the morning planner without a new model request')
            planner.sys.argv.append('--force')
            planner.main()
            self.assertEqual(len(requests),3)
            self.assertNotIn('editorial_reviewed_at',json.loads(path.read_text()))


if __name__ == '__main__':unittest.main()
