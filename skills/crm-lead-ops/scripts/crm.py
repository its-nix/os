#!/usr/bin/env python3
"""Deterministic Twenty CRM operations for a venture lead (source of truth for leads/pipeline).

  crm.py intake  --email E --first F --last L [--company C] [--source S]      # idempotent by email
  crm.py qualify --email E --stage SCREENING|MEETING|PROPOSAL|CUSTOMER|LOST --rationale TEXT
  crm.py note    --email E --title T --body TEXT
  crm.py show    --email E
  crm.py summary --venture ID [--since ISO_DATETIME] [--until ISO_DATETIME]

Uses TWENTY_URL (default http://127.0.0.1:3020) and the venture's workspace-scoped TWENTY_API_KEY
from the lead profile's private .env. Internal CRM writes are autonomous; anything that contacts
the lead is an external action and goes through approval.py instead.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
from statistics import median
import sys
import urllib.parse
import urllib.request

STAGES = ('NEW', 'SCREENING', 'MEETING', 'PROPOSAL', 'CUSTOMER')  # Twenty's default pipeline
DEFAULT_URL = 'http://127.0.0.1:3020'
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')


@contextmanager
def title_lock(title):
    """Serialize writers of one keyed note on this host (one Hermes runtime)."""
    base = Path(os.environ.get('HERMES_HOME', '/opt/data')) / 'ventures' / '.crm-locks'
    base.mkdir(parents=True, exist_ok=True)
    with open(base / hashlib.sha256(title.encode()).hexdigest()[:24], 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


class Twenty:
    def __init__(self, url=None, key=None, opener=None):
        self.url = (url or os.environ.get('TWENTY_URL') or DEFAULT_URL).rstrip('/')
        self.key = key or os.environ.get('TWENTY_API_KEY')
        if not self.key:
            raise SystemExit('TWENTY_API_KEY is not configured for this venture lead (blocked: founder setup)')
        self.opener = opener or self._http

    def _http(self, method, path, body=None):
        request = urllib.request.Request(self.url + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Authorization': 'Bearer ' + self.key,
                                                  'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    @staticmethod
    def _payload(reply):
        data = reply.get('data', reply)
        return next(iter(data.values())) if isinstance(data, dict) and len(data) == 1 else data

    def find_person(self, email):
        query = urllib.parse.quote(f'emails.primaryEmail[eq]:"{email}"')
        people = self._payload(self.opener('GET', f'/rest/people?filter={query}&limit=1'))
        return people[0] if people else None

    def create(self, obj, body):
        return self._payload(self.opener('POST', f'/rest/{obj}', body))

    def update(self, obj, rid, body):
        return self._payload(self.opener('PATCH', f'/rest/{obj}/{rid}', body))

    def opportunity_for(self, person_id):
        query = urllib.parse.quote(f'pointOfContactId[eq]:"{person_id}"')
        found = self._payload(self.opener('GET', f'/rest/opportunities?filter={query}&limit=1'))
        return found[0] if found else None

    def rows(self, obj, **filters):
        params = {'limit': 60, 'depth': 0, **filters}
        seen = set()
        while True:
            reply = self.opener('GET', f'/rest/{obj}?' + urllib.parse.urlencode(params))
            rows = reply['data'][obj]
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError('Invalid CRM response')
            yield from rows
            page = reply.get('pageInfo', {})
            if not page.get('hasNextPage'):
                return
            cursor = page.get('endCursor')
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError('Invalid CRM cursor')
            seen.add(cursor)
            params['starting_after'] = cursor

    def find_note(self, title):
        query = urllib.parse.quote(f'title[eq]:"{title}"')
        found = self._payload(self.opener('GET', f'/rest/notes?filter={query}&limit=1'))
        return found[0] if found else None

    def note(self, person_id, title, body, opportunity_id=None):
        """Idempotent by title (callers put a unique key in it); links are repaired on retry."""
        with title_lock(title):
            note = self.find_note(title) or self.create('notes', {'title': title, 'bodyV2': {'markdown': body}})
            query = urllib.parse.quote(f'noteId[eq]:"{note["id"]}"')
            linked = self._payload(self.opener('GET', f'/rest/noteTargets?filter={query}&limit=20')) or []
            have = {(t.get('targetPersonId'), t.get('targetOpportunityId')) for t in linked}
            if not any(p == person_id for p, _ in have):
                self.create('noteTargets', {'noteId': note['id'], 'targetPersonId': person_id})
            if opportunity_id and not any(o == opportunity_id for _, o in have):
                self.create('noteTargets', {'noteId': note['id'], 'targetOpportunityId': opportunity_id})
            return note


def intake(crm, args):
    person = crm.find_person(args.email)
    created = person is None
    if created:
        person = crm.create('people', {'name': {'firstName': args.first, 'lastName': args.last},
                                       'emails': {'primaryEmail': args.email}})
    opportunity = crm.opportunity_for(person['id'])
    if opportunity is None:
        opportunity = crm.create('opportunities', {'name': f'{args.first} {args.last}'.strip() + (
            f' ({args.company})' if args.company else ''), 'stage': 'NEW', 'pointOfContactId': person['id']})
    # Re-run safe: the intake note is keyed by email, so a crash before it is healed on retry.
    crm.note(person['id'], f'Lead intake: {args.email}', f'Source: {args.source or "unspecified"}', opportunity['id'])
    return {'person': person['id'], 'opportunity': opportunity['id'], 'created': created}


def require(crm, email):
    person = crm.find_person(email)
    if not person:
        raise SystemExit('No CRM record for this email; run intake first')
    return person, crm.opportunity_for(person['id'])


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def summary_time(value):
    parsed = timestamp(value)
    if parsed is None:
        raise argparse.ArgumentTypeError('expected an ISO 8601 timestamp')
    return parsed


def stage_timings(crm, opportunities, since, until):
    """Measure recorded qualification, using the keyed notes written by qualify."""
    notes = {}
    for row in crm.rows('notes', filter='title[startsWith]:"Qualified "'):
        title = row.get('title')
        if not isinstance(title, str) or not title.startswith('Qualified '):
            continue
        stage = title.partition(':')[0].removeprefix('Qualified ')
        at = timestamp(row.get('createdAt'))
        if stage in STAGES[1:] and at and since <= at < until:
            notes[row['id']] = (stage, at)
    if not notes:
        return {}
    samples = {}
    for target in crm.rows('noteTargets'):
        note = notes.get(target.get('noteId'))
        opportunity = opportunities.get(target.get('targetOpportunityId'))
        if not note or not opportunity:
            continue
        stage, at = note
        created = timestamp(opportunity.get('createdAt'))
        if created and created <= at:
            key = (stage, opportunity.get('pointOfContactId') or opportunity['id'])
            minutes = (at - created).total_seconds() / 60
            samples[key] = min(minutes, samples.get(key, minutes))
    result = {}
    for stage in STAGES[1:]:
        minutes = [value for (name, _), value in samples.items() if name == stage]
        if minutes:
            result[stage] = {'samples': len(minutes), 'median_minutes': round(median(minutes), 2)}
    return result


def summary(crm, since, until):
    opportunities = {row['id']: row for row in crm.rows('opportunities')}
    stages = dict.fromkeys((*STAGES, 'LOST', 'OTHER'), 0)
    leads = {}
    for row in opportunities.values():
        leads.setdefault(row.get('pointOfContactId') or row['id'], []).append(row)
    new = 0
    dates_complete = True
    for rows in leads.values():
        dated = [(timestamp(row.get('createdAt')), row['id'], row) for row in rows]
        dates_complete &= all(at is not None for at, _, _ in dated)
        dated.sort(key=lambda item: (item[0] or datetime.min.replace(tzinfo=timezone.utc), item[1]))
        first, _, _ = dated[0]
        new += bool(first and since <= first < until)
        stage = dated[-1][2].get('stage')
        stages[stage if stage in stages else 'OTHER'] += 1
    try:
        timings = stage_timings(crm, opportunities, since, until)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        timings = None  # A scoped API role may only permit opportunities.
    return {'status': 'ok', 'stages': stages, 'new_leads': new if dates_complete else None,
            'qualified_leads': sum(stages[stage] for stage in STAGES[1:]), 'stage_timings': timings}


def venture_summary(args, crm):
    home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    if home.parent.name == 'profiles':
        home = home.parent.parent
    try:
        manifest = json.loads((home / 'ventures' / args.venture / 'manifest.json').read_text())
        if manifest.get('id') != args.venture:
            return {'status': 'unavailable'}
        if manifest.get('integrations', {}).get('crm', {}).get('enabled') is not True:
            return {'status': 'disabled'}
        if crm is None:
            profile = manifest.get('lead_profile', '')
            if not isinstance(profile, str) or not SLUG.fullmatch(profile):
                return {'status': 'unavailable'}
            settings = {}
            for line in (home / 'profiles' / profile / '.env').read_text().splitlines():
                name, _, value = line.partition('=')
                name = name.strip().removeprefix('export ').strip()
                if name in ('TWENTY_API_KEY', 'TWENTY_URL'):
                    parts = shlex.split(value, comments=True)
                    settings[name] = parts[0] if len(parts) == 1 else ''
            if not settings.get('TWENTY_API_KEY'):
                return {'status': 'unavailable'}
            crm = Twenty(url=settings.get('TWENTY_URL') or DEFAULT_URL, key=settings['TWENTY_API_KEY'])
        until = args.until or datetime.now(timezone.utc)
        return summary(crm, args.since or until - timedelta(days=1), until)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'status': 'unavailable'}  # Never print a response, request URL or credential.


def main(argv=None, crm=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    i = sub.add_parser('intake')
    i.add_argument('--email', required=True)
    i.add_argument('--first', required=True)
    i.add_argument('--last', default='')
    i.add_argument('--company')
    i.add_argument('--source')
    q = sub.add_parser('qualify')
    q.add_argument('--email', required=True)
    q.add_argument('--stage', required=True, choices=STAGES[1:])
    q.add_argument('--rationale', required=True)
    n = sub.add_parser('note')
    n.add_argument('--email', required=True)
    n.add_argument('--title', required=True)
    n.add_argument('--body', required=True)
    s = sub.add_parser('show')
    s.add_argument('--email', required=True)
    s = sub.add_parser('summary')
    s.add_argument('--venture', required=True)
    s.add_argument('--since', type=summary_time)
    s.add_argument('--until', type=summary_time)
    args = parser.parse_args(argv)
    if args.command == 'summary':
        if not SLUG.fullmatch(args.venture):
            parser.error('summary requires a venture slug')
        if args.since and args.until and args.since >= args.until:
            parser.error('summary requires --since before --until')
        print(json.dumps(venture_summary(args, crm), indent=2))
        return 0
    crm = crm or Twenty()
    if args.command == 'intake':
        result = intake(crm, args)
    else:
        person, opportunity = require(crm, args.email)
        if args.command == 'qualify':
            if opportunity is None:
                raise SystemExit('No opportunity for this lead; run intake first')
            crm.update('opportunities', opportunity['id'], {'stage': args.stage})
            crm.note(person['id'], f'Qualified {args.stage}: {args.email}', args.rationale, opportunity['id'])
            result = {'person': person['id'], 'opportunity': opportunity['id'], 'stage': args.stage}
        elif args.command == 'note':
            note = crm.note(person['id'], args.title, args.body, opportunity and opportunity['id'])
            result = {'person': person['id'], 'note': note['id']}
        else:
            result = {'person': person, 'opportunity': opportunity}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
