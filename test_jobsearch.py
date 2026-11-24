from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_sources import canonical_url, collect_source, fetch, plain, SourceError
from jobsearch import JobSearch, assess, experience

PROFILE = {'skills':['TypeScript','React','Node.js','PostgreSQL','REST APIs','Redis'],
           'listed_only_skills':['Docker'], 'locations':['Pune','Bengaluru','Hyderabad','Mumbai'], 'remote_india':True}
NOW = datetime(2026,11,23,3,30,tzinfo=timezone.utc)


def job(**changes):
    return dict(source_id='one',company='Example',title='Software Engineer',location='Pune, India',
                description='Requirements\n0-2 years of professional experience\nTypeScript React Node.js PostgreSQL REST APIs Redis Docker',
                url='https://example.com/jobs/1',salary=None,workplace='OnSite',employment='FullTime', **changes)


class MatchingTests(unittest.TestCase):
    def test_experience_ranges_and_headers(self):
        self.assertEqual(experience('Experience:\n0-2 years')[:2], (0,2))
        self.assertEqual(experience('Pune Hybrid Full-time 2–4 years')[:2], (2,4))
        self.assertEqual(experience('Freshers welcome, up to 2 years.')[:2], (0,2))
        self.assertEqual(experience('0 to 2 years of professional experience')[:2], (0,2))
        self.assertEqual(experience('Junior (1-3 Years)')[:2], (1,3))

    def test_required_overrides_generic_header_but_not_optional(self):
        self.assertEqual(experience('Experience 1-3 years\nMust have 5+ years of professional Java experience')[0],5)
        self.assertEqual(experience('0-2 years experience\nNice-to-have:\n5 years Python experience')[0],0)
        self.assertIsNone(experience('Our company was founded 10 years ago.')[0])

    def test_unknown_stays_unknown(self):
        j=job();j['description']='React Node.js TypeScript REST APIs for web applications; AWS is optional.'
        a=assess(j,PROFILE)
        self.assertTrue(a['eligible']); self.assertEqual(a['fit'],'possible');self.assertIsNone(a['experience_min'])
        self.assertIsNone(a['salary'])

    def test_primary_secondary_and_exclusions(self):
        self.assertEqual(assess(job(),PROFILE)['priority'],'primary')
        j=job();j['title']='NestJS Backend Developer'; self.assertEqual(assess(j,PROFILE)['priority'],'secondary')
        for title in ['Senior Full Stack Engineer','Software Engineer III','Software Engineer, Frontend','DevOps Software Engineer','Software Engineer Intern']:
            j=job();j['title']=title;self.assertFalse(assess(j,PROFILE)['eligible'],title)
        j=job();j['title']='Software Engineer I, Full Stack (Frontend Focused)';self.assertTrue(assess(j,PROFILE)['eligible'])

    def test_remote_country_and_location(self):
        for location, expected in [('Remote, US',False),('Remote',False),('Remote, India',True),('Bangalore, India',True),('Gurugram, India',False),('Worldwide remote',True)]:
            j=job();j['location']=location; j['workplace']='Remote' if 'remote' in location.lower() else 'OnSite'
            self.assertEqual(assess(j,PROFILE)['eligible'],expected,location)

    def test_notice_and_salary_are_not_hard_filters(self):
        j=job();j['description']+='\nImmediate joiners only. Salary ₹5L to ₹6L.'
        a=assess(j,PROFILE)
        self.assertTrue(a['eligible']);self.assertIn('Immediate', ' '.join(a['gaps']));self.assertEqual(a['salary'],'₹5L to ₹6L')

    def test_senior_requirement_and_foreign_stack(self):
        j=job();j['description']+='\n5+ years of Node.js experience required.'
        self.assertFalse(assess(j,PROFILE)['eligible'])
        j=job();j['description']='1-2 years experience. React TypeScript REST APIs Java Spring Boot.'
        self.assertFalse(assess(j,PROFILE)['eligible'])


class CollectorTests(unittest.TestCase):
    def test_safe_url_and_tracking(self):
        self.assertEqual(canonical_url('https://example.com/job?id=5&utm_source=x#apply'),'https://example.com/job?id=5')
        for url in ['http://example.com','https://u:p@example.com','javascript:alert(1)','https://example.com:22/']:
            with self.assertRaises(ValueError): canonical_url(url)

    def test_private_network_is_not_fetched(self):
        with patch('job_sources.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(SourceError):fetch('https://example.com')

    def test_missing_board_is_not_empty_success(self):
        s={'kind':'ashby','url':'https://example.com','id':'one','company':'Example'}
        for payload in ['{}','{"jobs":{}}','{"jobs":[{}]}']:
            with self.assertRaises((ValueError,KeyError)):collect_source(s,lambda _:payload)
        self.assertEqual(collect_source(s,lambda _:'{"jobs":[]}'),[])

    def test_ashby_listing_and_pay(self):
        s={'kind':'ashby','url':'https://example.com','id':'one','company':'Example'}
        j={'title':'Software Engineer','jobUrl':'https://example.com/job','location':'Bengaluru','descriptionPlain':job()['description'],
           'compensation':{'scrapeableCompensationSalarySummary':'₹15L - ₹30L'},'isListed':True}
        rows=collect_source(s,lambda _:json.dumps({'jobs':[j]}));self.assertEqual(rows[0]['salary'],'₹15L - ₹30L')
        j['isListed']=False;self.assertEqual(collect_source(s,lambda _:json.dumps({'jobs':[j]})),[])

    def test_partial_greenhouse_and_no_description_fail(self):
        s={'kind':'greenhouse','url':'https://example.com','id':'one','company':'Example'}
        with self.assertRaises(SourceError):collect_source(s,lambda _:'{"jobs":[],"meta":{"total":3}}')

    def test_page_boundaries_and_removed_listing(self):
        s={'kind':'page','url':'https://example.com/jobs','id':'one','company':'Example',
           'openings':[{'title':'Full Stack Engineer','location':'Pune','start':'Full Stack Engineer','end':'Next job','require':['Node.js']}]}
        html='<nav>Full Stack Engineer Java LangChain 8+ years experience</nav><h1>Full Stack Engineer</h1><p>'+job()['description']+'</p><h2>Next job</h2><p>15+ years experience</p>'
        rows=collect_source(s,lambda _:html);self.assertEqual(experience(rows[0]['description'])[0],0)
        self.assertNotIn('LangChain',rows[0]['description'])
        with self.assertRaises(SourceError):collect_source(s,lambda _:html.replace('Next job','Changed layout'))
        self.assertEqual(collect_source(s,lambda _:html.replace('</p>',' This position is closed.</p>',1)),[])

    def test_html_instructions_stay_data_and_scripts_removed(self):
        self.assertEqual(plain('<p>React</p><script>steal()</script><p>Node</p>'),'React\nNode')


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=JobSearch(self.temp.name)
        (Path(self.temp.name)/'profile.json').write_text(json.dumps(PROFILE))
        self.sources=[{'id':'one','company':'Example'}]

    def tearDown(self):self.temp.cleanup()

    def sync(self,jobs=None,ok=True,now=NOW):
        return self.store.sync(self.sources,now,lambda _:[{'id':'one','company':'Example','ok':ok,'jobs':jobs if jobs is not None else [job()],'error':None if ok else 'TimeoutError'}])

    def test_dedup_retains_status_and_original_first_seen(self):
        self.sync();j=self.store.listing(NOW)['jobs'][0];self.store.set_status(j['id'],'saved')
        self.sync(now=NOW+timedelta(days=1));rows=self.store.listing(NOW+timedelta(days=1))['jobs']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'saved');self.assertEqual(rows[0]['first_seen'],NOW.isoformat())

    def test_failed_source_unverified_not_closed(self):
        self.sync();self.sync(ok=False,now=NOW+timedelta(days=1))
        self.assertEqual(self.store.listing(NOW+timedelta(days=1))['jobs'][0]['listing_state'],'unverified')
        self.assertNotIn('1. Example',self.store.digest(NOW+timedelta(days=1)))

    def test_successful_absence_closes_and_reopen_keeps_application(self):
        self.sync();j=self.store.listing(NOW)['jobs'][0];self.store.set_status(j['id'],'applied')
        self.sync([],now=NOW+timedelta(days=1));self.assertEqual(self.store.listing(NOW+timedelta(days=1))['jobs'][0]['listing_state'],'closed')
        self.sync(now=NOW+timedelta(days=2));j=self.store.listing(NOW+timedelta(days=2))['jobs'][0]
        self.assertEqual(j['listing_state'],'active');self.assertEqual(j['status'],'applied')

    def test_stale_observation_and_invalid_tracker_status(self):
        self.sync();self.assertEqual(self.store.listing(NOW+timedelta(days=2))['jobs'][0]['listing_state'],'unverified')
        with self.assertRaises(ValueError):self.store.set_status('../bad','applied')
        with self.assertRaises(ValueError):self.store.set_status('a'*20,'submit')
        with self.assertRaises(ValueError):self.store.set_status('a'*20,'applied')

    def test_digest_preview_does_not_reserve_daily_does_once(self):
        self.sync();self.assertIn('1. Example',self.store.digest(NOW))
        self.assertIsNone(self.store.listing(NOW)['jobs'][0]['reserved_at'])
        self.assertIn('1. Example',self.store.digest(NOW,reserve=True));self.assertEqual(self.store.digest(NOW,reserve=True),'')
        self.sync(now=NOW+timedelta(days=1));self.assertNotIn('1. Example',self.store.digest(NOW+timedelta(days=1),reserve=True))

    def test_stale_digest_refused_and_applied_excluded(self):
        self.sync();j=self.store.listing(NOW)['jobs'][0];self.store.set_status(j['id'],'applied')
        self.assertNotIn('1. Example',self.store.digest(NOW))
        with self.assertRaises(ValueError):self.store.digest(NOW+timedelta(hours=3))

    def test_company_diversity(self):
        jobs=[]
        for i in range(8):
            j=job();j['url']=f'https://example.com/jobs/{i}';j['company']='Example' if i<6 else f'Other{i}';jobs.append(j)
        self.sync(jobs);text=self.store.digest(NOW)
        self.assertEqual(text.count('. Example -'),2);self.assertIn('Other6',text)

    def test_updated_mandatory_experience_removes_eligibility(self):
        self.sync();j=job();j['description']+='\n5+ years experience required.';self.sync([j],now=NOW+timedelta(days=1))
        row=self.store.listing(NOW+timedelta(days=1))['jobs'][0]
        self.assertFalse(row['assessment']['eligible']);self.assertNotIn('1. Example',self.store.digest(NOW+timedelta(days=1)))


if __name__=='__main__':unittest.main()
