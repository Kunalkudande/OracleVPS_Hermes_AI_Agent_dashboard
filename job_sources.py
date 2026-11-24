"""Bounded, read-only employer feeds. No browser sessions or application submission."""
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class SourceError(ValueError):
    pass


def canonical_url(url):
    if not isinstance(url, str) or len(url) > 2000:
        raise SourceError('Invalid source URL')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise SourceError('Only public HTTPS URLs are supported')
    # Keep non-tracking query parameters: some employers use a query job identifier.
    from urllib.parse import parse_qsl, urlencode
    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith(('utm_', 'ref')) and k.lower() not in ('source', 'gh_src')])
    return urlunsplit(('https', parsed.netloc.lower(), parsed.path.rstrip('/') or '/', query, ''))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceError('Redirect requires source configuration review')


def fetch(url):
    canonical_url(url)
    host = urlsplit(url).hostname
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise SourceError('Non-public source rejected')
    request = Request(url, headers={'User-Agent': 'HermesPersonalJobWatch/1.0', 'Accept': 'application/json,text/html', 'Accept-Encoding': 'identity'})
    with build_opener(NoRedirect).open(request, timeout=20) as response:
        content = response.read(4_000_001)
        if len(content) > 4_000_000:
            raise SourceError('Source exceeds 4 MB limit')
        return content.decode('utf-8', errors='replace')


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip += 1
        if tag in ('p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'tr') and not self.skip:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip = max(0, self.skip - 1)
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'section') and not self.skip:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain(value):
    parser = TextParser()
    parser.feed(unescape(str(value or '')))
    return '\n'.join(line for raw in ''.join(parser.parts).splitlines() if (line := re.sub(r'\s+', ' ', raw).strip()))


def row(source, title, location, description, url, **extra):
    return dict(source_id=source['id'], company=source['company'], title=str(title)[:180],
                location=str(location)[:250], description=description[:24000], url=canonical_url(url),
                salary=extra.pop('salary', None), **extra)


def collect_source(source, getter=fetch):
    """A malformed source is a failure, never a successful empty board."""
    kind, url = source['kind'], source['url']
    raw = getter(url)
    if kind in ('ashby', 'greenhouse', 'lever'):
        data = json.loads(raw)
        jobs = data if kind == 'lever' else data.get('jobs')
        if not isinstance(jobs, list) or len(jobs) > 3000:
            raise SourceError('Unsupported or incomplete board response')
        if kind == 'greenhouse' and data.get('meta', {}).get('total', len(jobs)) != len(jobs):
            raise SourceError('Partial board response')
        result = []
        for j in jobs:
            if not isinstance(j, dict):
                raise SourceError('Invalid job entry')
            if kind == 'ashby':
                if not j.get('isListed', True):
                    continue
                locations = [j.get('location', '')] + [x.get('location', '') for x in j.get('secondaryLocations', [])]
                address = j.get('address', {}).get('postalAddress', {}) if isinstance(j.get('address'), dict) else {}
                country = address.get('addressCountry', '')
                location = ' / '.join(filter(None, locations))
                if country:
                    location += ' (' + country + ')'
                result.append(row(source, j['title'], location, j.get('descriptionPlain') or plain(j.get('descriptionHtml')), j['jobUrl'],
                                  workplace=j.get('workplaceType', ''), employment=j.get('employmentType', ''),
                                  published_at=j.get('publishedAt'), salary=(j.get('compensation') or {}).get('scrapeableCompensationSalarySummary')))
            elif kind == 'greenhouse':
                result.append(row(source, j['title'], j.get('location', {}).get('name', ''), plain(j.get('content')), j['absolute_url'],
                                  workplace='', employment='', published_at=None))
            else:
                sections = '\n'.join(plain(x.get('text', '') + '\n' + x.get('content', '')) for x in j.get('lists', []))
                result.append(row(source, j['text'], j.get('categories', {}).get('location', ''),
                                  plain(j.get('descriptionPlain') or j.get('description')) + '\n' + sections + '\n' + plain(j.get('additionalPlain')),
                                  j['hostedUrl'], workplace=j.get('workplaceType', ''), employment=j.get('categories', {}).get('commitment', ''), published_at=None))
        if any(not x['title'] or len(x['description']) < 60 for x in result):
            raise SourceError('Board omitted job descriptions')
        return result
    if kind != 'page':
        raise SourceError('Unsupported source type')
    # Ignore navigation and page metadata before the actual main heading.
    heading = re.search(r'<h1\b', raw, re.I)
    text = plain(raw[heading.start():] if heading else raw)
    results = []
    for opening in source['openings']:
        # Delimit individual postings, so other vacancies cannot alter their requirements.
        start = text.find(opening['start'])
        if start < 0:
            raise SourceError('Expected role section missing; review page layout')
        segment = text[start:]
        end = segment.find(opening['end'], len(opening['start'])) if opening.get('end') else len(segment)
        if end < 0:
            raise SourceError('Role boundary missing; review page layout')
        segment = segment[:end]
        if len(segment) < 100 or len(segment) > 24000:
            raise SourceError('Role section length invalid')
        if re.search(r'(?:no longer (?:accepting|available)|position (?:is )?(?:closed|filled)|job (?:is )?closed)', segment, re.I):
            continue
        if any(marker.lower() not in segment.lower() for marker in opening.get('require', [])):
            raise SourceError('Role evidence changed; review source configuration')
        results.append(row(source, opening['title'], opening['location'], segment, opening.get('url', url),
                           workplace=opening.get('workplace', ''), employment='FullTime', published_at=None))
    return results


def collect_all(sources, getter=fetch):
    def one(source):
        try:
            return {'id': source['id'], 'company': source['company'], 'ok': True, 'jobs': collect_source(source, getter)}
        except Exception as error:
            # Exception strings can contain URL credentials or network details.
            return {'id': source['id'], 'company': source['company'], 'ok': False, 'jobs': [], 'error': type(error).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, sources))
