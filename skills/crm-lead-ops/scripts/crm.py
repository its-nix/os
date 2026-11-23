#!/usr/bin/env python3
"""Deterministic Twenty CRM operations for a venture lead (source of truth for leads/pipeline).

  crm.py intake  --email E --first F --last L [--company C] [--source S]      # idempotent by email
  crm.py qualify --email E --stage SCREENING|MEETING|PROPOSAL|CUSTOMER|LOST --rationale TEXT
  crm.py note    --email E --title T --body TEXT
  crm.py show    --email E

Uses TWENTY_URL (default http://127.0.0.1:3020) and the venture's workspace-scoped TWENTY_API_KEY
from the lead profile's private .env. Internal CRM writes are autonomous; anything that contacts
the lead is an external action and goes through approval.py instead.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

STAGES = ('NEW', 'SCREENING', 'MEETING', 'PROPOSAL', 'CUSTOMER')  # Twenty's default pipeline


class Twenty:
    def __init__(self, url=None, key=None, opener=None):
        self.url = (url or os.environ.get('TWENTY_URL') or 'http://127.0.0.1:3020').rstrip('/')
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

    def find_note(self, title):
        query = urllib.parse.quote(f'title[eq]:"{title}"')
        found = self._payload(self.opener('GET', f'/rest/notes?filter={query}&limit=1'))
        return found[0] if found else None

    def note(self, person_id, title, body, opportunity_id=None):
        """Idempotent by title: callers put a unique key (lead email, action id) in the title."""
        existing = self.find_note(title)
        if existing:
            return existing
        note = self.create('notes', {'title': title, 'bodyV2': {'markdown': body}})
        self.create('noteTargets', {'noteId': note['id'], 'targetPersonId': person_id})
        if opportunity_id:
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
    args = parser.parse_args(argv)
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
