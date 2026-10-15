"""Private, locked monitoring documents and idempotent alert episodes."""
from dataclasses import asdict
from hashlib import sha256
import json
from uuid import uuid4

from portfolio_app.atomic_file import staged_text
from portfolio_app.holdings import DataError
from portfolio_app.monitoring import Rule, SeriesSpec, Evaluation, utc_now
from portfolio_app.storage import save_document
from portfolio_app.workspace_lock import workspace_lock

FILE = 'monitoring.json'


def empty_document():
    return {'version': 1, 'rules': [], 'definitions': {}, 'observations': [], 'events': [], 'states': {}, 'notice_seen': []}


def rule_from_dict(raw):
    raw = dict(raw)
    raw['series'] = SeriesSpec(**raw['series']) if raw.get('series') else None
    raw['backing'] = tuple(raw.get('backing', ()))
    raw['cash'] = tuple(raw.get('cash', ()))
    return Rule(**raw)


def load_monitoring(directory):
    path = directory / FILE
    if not path.exists():
        return empty_document(), None
    try:
        content = path.read_bytes()
        raw = json.loads(content, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        if not isinstance(raw, dict) or raw.get('version') != 1:
            raise ValueError('Unsupported monitoring version')
        if any(not isinstance(raw.get(k), list) for k in ('rules', 'observations', 'events', 'notice_seen')) or any(not isinstance(raw.get(k), dict) for k in ('states', 'definitions')):
            raise ValueError('Invalid monitoring document')
        json.dumps(raw, allow_nan=False)
        if any(not isinstance(x, str) for x in raw['notice_seen']):
            raise ValueError('Invalid notification identities')
        rules = [rule_from_dict(r) for r in raw['rules']]
        if len({r.id for r in rules}) != len(rules):
            raise ValueError('Duplicate rule identity')
        for definition in raw['definitions'].values():
            rule_from_dict(definition)
        for row in raw['observations']:
            if not isinstance(row, dict) or not all(k in row for k in ('rule_id', 'revision', 'fingerprint', 'evaluation', 'recorded_at')):
                raise ValueError('Invalid monitoring observation')
            Evaluation(**row['evaluation'])
            if f"{row['rule_id']}:{row['revision']}" not in raw['definitions'] or type(row.get('recovered')) is not bool:
                raise ValueError('Observation has no valid rule definition')
        for event in raw['events']:
            if not isinstance(event, dict) or not all(k in event for k in ('id', 'rule_id', 'revision', 'condition', 'observed_at', 'acknowledged', 'read')):
                raise ValueError('Invalid monitoring event')
            Evaluation(**event['evaluation'])
            if event['condition'] not in {'approaching','triggered'} or any(type(event.get(k)) is not bool for k in ('acknowledged','read','resolved','recovered')):
                raise ValueError('Invalid alert state')
            if f"{event['rule_id']}:{event['revision']}" not in raw['definitions'] or not isinstance(event.get('name'), str):
                raise ValueError('Alert has no valid rule definition')
        for identity, state in raw['states'].items():
            if not isinstance(state, dict) or identity not in {r.id for r in rules}:
                raise ValueError('Invalid rule state')
            Evaluation(**state['latest'])
            if 'last_valid' in state:
                Evaluation(**state['last_valid'])
            if state.get('condition', 'normal') not in {'normal','approaching','triggered'}:
                raise ValueError('Invalid rule condition')
        return raw, sha256(content).hexdigest()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DataError('Invalid monitoring.json; restore it from a backup. The existing file was retained.') from exc


def _write(directory, document, revision, *, backup=False):
    content = json.dumps(document, allow_nan=False, sort_keys=True, indent=2)+'\n'
    if backup:
        save_document(directory / FILE, content, revision)
    else:
        # Observations are frequent; do not duplicate the entire growing log into
        # a new backup every minute. All writers hold the workspace lock.
        path = directory / FILE
        current = sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current != revision:
            raise DataError('Monitoring changed during this update; retry.')
        directory.mkdir(parents=True, exist_ok=True)
        with staged_text(path, lambda handle: handle.write(content), sync=True) as temporary:
            temporary.replace(path)


def save_rule(directory, rule, *, expected_rule_revision=None):
    with workspace_lock(directory):
        doc, revision = load_monitoring(directory)
        old = next((r for r in doc['rules'] if r['id'] == rule.id), None)
        if (old['revision'] if old else None) != expected_rule_revision:
            raise DataError('This rule changed in another editor. Reopen it before saving.')
        if rule.revision != (old['revision']+1 if old else 1):
            raise DataError('The new rule revision is invalid.')
        doc['rules'] = [r for r in doc['rules'] if r['id'] != rule.id]+[asdict(rule)]
        doc['definitions'][f'{rule.id}:{rule.revision}'] = asdict(rule)
        doc['states'].pop(rule.id, None)
        for event in doc['events']:
            if event['rule_id'] == rule.id:
                event['resolved'] = True
        _write(directory, doc, revision, backup=True)


def delete_rule(directory, identity, expected_rule_revision):
    with workspace_lock(directory):
        doc, revision = load_monitoring(directory)
        old = next((r for r in doc['rules'] if r['id'] == identity), None)
        if old is None or old['revision'] != expected_rule_revision:
            raise DataError('This rule changed. Reload before deleting it.')
        doc['rules'] = [r for r in doc['rules'] if r['id'] != identity]
        doc['states'].pop(identity, None)
        for event in doc['events']:
            if event['rule_id'] == identity:
                event['resolved'] = True
        _write(directory, doc, revision, backup=True)


def record_evaluations(directory, rule, evaluations, *, recovered=False, now=None):
    now = now or utc_now()
    with workspace_lock(directory):
        doc, revision = load_monitoring(directory)
        current = next((r for r in doc['rules'] if r['id'] == rule.id), None)
        if not current or current['revision'] != rule.revision or not current['enabled']:
            return False
        state = doc['states'].get(rule.id, {})
        changed = False
        for evaluation in evaluations:
            raw = asdict(evaluation)
            # Account calculations use the observation time of the inputs, not
            # the polling clock, to avoid logging unchanged snapshots repeatedly.
            fingerprint = sha256(json.dumps(raw, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if state.get('fingerprint') == fingerprint:
                continue
            previous = state.get('condition', 'normal')
            state = {**state, 'fingerprint': fingerprint, 'quality': evaluation.quality,
                     'latest': raw, 'checked_at': now.isoformat(), 'revision': rule.revision}
            doc['observations'].append(dict(rule_id=rule.id, revision=rule.revision,
                fingerprint=fingerprint, evaluation=raw, recorded_at=now.isoformat(), recovered=recovered))
            if evaluation.quality in {'valid', 'not_applicable'}:
                current_condition = evaluation.condition if evaluation.quality == 'valid' else 'normal'
                state['condition'] = current_condition
                if evaluation.quality == 'valid':
                    state['last_valid'] = raw
                # One episode lasts until full recovery to normal. Escalation
                # gets its own alert but jitter inside the warning zone does not.
                if current_condition == 'normal':
                    state.pop('episode', None)
                    state['notified'] = []
                    for event in doc['events']:
                        if event['rule_id'] == rule.id and event['revision'] == rule.revision:
                            event['resolved'] = True
                elif current_condition not in state.get('notified', []):
                    state.setdefault('episode', uuid4().hex)
                    state['notified'] = [*state.get('notified', []), current_condition]
                    doc['events'].append(dict(id=uuid4().hex, rule_id=rule.id, revision=rule.revision,
                        episode=state['episode'], name=rule.name, condition=current_condition,
                        observed_at=evaluation.observed_at, recorded_at=now.isoformat(), evaluation=raw,
                        read=False, acknowledged=False, resolved=False, recovered=recovered))
                state['previous_condition'] = previous
            doc['states'][rule.id] = state
            changed = True
        if changed:
            _write(directory, doc, revision)
        return changed


def mark_events(directory, identities, *, acknowledge=False):
    if not identities:
        return
    with workspace_lock(directory):
        doc, revision = load_monitoring(directory)
        changed = False
        for event in doc['events']:
            if event['id'] in identities:
                changed |= not event['read'] or (acknowledge and not event['acknowledged'])
                event['read'] = True
                if acknowledge:
                    event['acknowledged'] = True
        if changed:
            _write(directory, doc, revision)


def remember_notices(directory, current, *, opened=False):
    initial, _ = load_monitoring(directory)
    retained = set(current) if opened else set(initial['notice_seen']) & set(current)
    if retained == set(initial['notice_seen']):
        return retained
    with workspace_lock(directory):
        doc, revision = load_monitoring(directory)
        seen = set(doc['notice_seen']) & set(current)
        if opened:
            seen = set(current)
        if seen != set(doc['notice_seen']):
            doc['notice_seen'] = sorted(seen)
            _write(directory, doc, revision)
        return seen
