import json
import unittest
from x_content_rules import text_weight, safe_unicode, make_payload, finish_text, editorial_check, MAX_REQUEST_BYTES


class ContentRules(unittest.TestCase):
    def test_emoji_weight_and_short_url_reservation(self):
        self.assertEqual(text_weight('a' * 278 + '😅'), 280)
        self.assertEqual(text_weight('a' * 279 + '😅'), 281)
        self.assertEqual(text_weight('https://x.co'), 23)
        self.assertGreaterEqual(text_weight('👨‍👩‍👧‍👦'), 2)

    def test_hidden_characters_rejected(self):
        safe_unicode('A nice cup of coffee ☕')
        with self.assertRaises(ValueError): safe_unicode('Hidden\u202e text')

    def test_retry_does_not_replay_large_outputs(self):
        source = {'id': 0, 'excerpt': 'x' * 100000, 'url': 'https://example.com/item', 'extra_body': 'x' * 100000}
        context = {'date': '2026-10-20', 'sources': [source] * 8, 'recent_posts': ['x' * 10000] * 100}
        payload = make_payload('s' * 12000, context, feedback='bad ' * 10000)
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False).encode()), MAX_REQUEST_BYTES)
        self.assertEqual(len(payload['messages']), 2)
        self.assertNotIn('extra_body', payload['messages'][1]['content'])
        compact = make_payload('system', context, compact=True)
        self.assertEqual(json.loads(compact['messages'][1]['content'])['sources'], [])
        self.assertLessEqual(compact['max_tokens'], 1800)

    def test_link_does_not_delete_the_post_body(self):
        body = 'A concrete observation. A second useful sentence.'
        self.assertEqual(finish_text(body, 'https://example.com/' + 'x' * 250), body + ' https://example.com/' + 'x' * 250)

    def test_conversation_and_emoji_policy(self):
        posts = [dict(text='One more video always sounds like a reasonable plan. 😅',kind='opinion',topic='everyday',bucket='relatable'),
                 dict(text='Try writing the first step before you close the tutorial.',kind='tip',topic='learning',bucket='practical'),
                 dict(text='Which small daily inconvenience would you delete first?',kind='opinion',topic='everyday',bucket='conversation'),
                 dict(text='A quiet group chat can be a pleasant part of a weekend.',kind='opinion',topic='culture',bucket='experiment')]
        editorial_check(posts)
        posts[2]['text']='Here is a generic announcement with no question.'
        with self.assertRaises(ValueError):editorial_check(posts)


if __name__ == '__main__': unittest.main()
