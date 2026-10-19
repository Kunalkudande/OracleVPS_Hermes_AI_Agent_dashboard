"""Shared limits and editorial checks for the original-post planner."""
import json
import re
import unicodedata

STRATEGY = 'human-conversation-v3'
BUCKETS = ['relatable', 'practical', 'conversation', 'experiment']
EMOJIS = ('😅', '🙂', '😂', '🤔', '🙃', '👀', '💡', '☕', '🏏', '✨')
MAX_REQUEST_BYTES = 16000


def text_weight(text):
    """Conservative upper bound: all non-ASCII weigh 2; URLs at least 23.

    X v3 counts many Unicode characters/emoji sequences more cheaply than this.
    Overcounting is intentional: we never depend on shortening a long source URL.
    """
    text = unicodedata.normalize('NFC', text)
    count = lambda value: sum(1 if ord(c) < 128 else 2 for c in value)
    total = count(text)
    for match in re.finditer(r'https?://\S+', text):
        total += max(0, 23 - count(match.group()))
    return total


def safe_unicode(text):
    for char in text:
        category = unicodedata.category(char)
        if category in ('Cs', 'Co', 'Cn') or (category in ('Cc', 'Cf') and char not in ('\n', '\u200d')):
            raise ValueError('Draft contains unsupported or hidden characters')


def finish_text(text, source_url=None):
    if not isinstance(text, str):
        raise ValueError('Draft text must be a string')
    text = unicodedata.normalize('NFC', text.strip())
    if source_url and source_url not in text:
        text += ' ' + source_url
    # Never silently cut away the interesting sentence just to fit a long link.
    return text


def editorial_check(posts):
    if not isinstance(posts, list) or len(posts) != 4 or any(not isinstance(p, dict) for p in posts):
        raise ValueError('Expected four post objects')
    if [p.get('bucket') for p in posts] != BUCKETS:
        raise ValueError('Use relatable, practical, conversation, experiment in order')
    if [posts[i].get('kind') for i in range(3)] != ['opinion', 'tip', 'opinion']:
        raise ValueError('Use an observation, a practical tip and a conversational opinion')
    texts = [p.get('text', '') for p in posts]
    if any(not isinstance(text, str) for text in texts):
        raise ValueError('Text must be a string')
    question = texts[2]
    if question.count('?') != 1 or not question.rstrip().endswith(('?', *EMOJIS)):
        raise ValueError('Conversation post needs one genuine unanswered question')
    remainder = question.split('?')[1].strip()
    if remainder and remainder not in EMOJIS:
        raise ValueError('Leave the conversational question unanswered')
    if sum('?' in text for text in texts) > 2:
        raise ValueError('Do not end every post with a question')
    emoji_count = sum(sum(text.count(emoji) for emoji in EMOJIS) for text in texts)
    if not 1 <= emoji_count <= 2 or any(sum(text.count(emoji) for emoji in EMOJIS) > 1 for text in texts):
        raise ValueError('Use one or two contextual emojis across the day, at most one per post')
    if len({p.get('topic') for p in posts}) < 2:
        raise ValueError('Vary subjects across the day')
    banned = r'\b(?:delve|leverage|survivorship bias|structured authority injection|game.changer|unlock your potential|the real question is|depth beats breadth|agree or disagree|comment below|drop a|retweet if|future you will thank|specific silence|specific panic)\b'
    if any(post.get('kind') == 'news' for post in posts):
        raise ValueError('Skip launch announcements and headline summaries; use an original everyday take')
    for text in texts:
        if re.search(banned, text, re.I) or re.search(r'\bnot (?:just|only)\b.*\b(?:but|it.s)\b', text, re.I):
            raise ValueError('Rewrite the jargon, slogan or engagement bait in plain everyday language')
        if re.search(r'\b(?:save|saves|saving)\b.{0,45}\b(?:seconds?|minutes?|hours?)\b', text, re.I):
            raise ValueError('Do not invent a measured time-saving benefit')
        if text.count('\n') > 2 or '#' in text:
            raise ValueError('Use at most one short paragraph break and no hashtags')


def make_payload(system, context, model='auto', feedback='', compact=False):
    # Project explicitly allowed fields. Full fetched article bodies never enter requests.
    sources = [] if compact else [
        {key: source.get(key) for key in ('id', 'title', 'url', 'published_at', 'fetched_at', 'excerpt')}
        for source in context.get('sources', [])[:3]
    ]
    for source in sources:
        source['title'] = str(source.get('title') or '')[:120]
        source['excerpt'] = str(source.get('excerpt') or '')[:700]
    data = {'date': context['date'], 'sources': sources,
            'recent_posts': [str(text)[:280] for text in context.get('recent_posts', [])[-(8 if compact else 16):]],
            'feedback': feedback[:240], 'news_optional': True}
    payload = {'model': model, 'messages': [{'role': 'system', 'content': system[:6500]},
                                          {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}],
               'max_tokens': 1400 if compact else 1800, 'temperature': .85, 'stream': False,
               'response_format': {'type': 'json_object'},
               'chat_template_kwargs': {'enable_thinking': False}}
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_REQUEST_BYTES:
        if not compact:
            return make_payload(system, context, model, feedback, True)
        raise ValueError('Planner request exceeded its bounded size')
    return payload
