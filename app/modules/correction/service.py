import json
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from app.errors import DataError
from app.pipeline import stages
from app.store import connection
from . import repository as store


def correction_id(run_id, key, revision):
    return sha256(f'correction:{run_id}:{key}:{revision}'.encode()).hexdigest()


def register(run_id, payload):
    """登记签字后到达的更高修订。只携带与原批已采信金额的差额；
    证据不足时保留待补证记录，同一补正重复登记不产生新记录。"""
    moment = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        run = store.get_run(db, run_id)
        version = store.signed_version(db, run_id)
        origin = json.loads(run['result'])
        baseline = {row['key']: row for row in origin['accepted']}
        result = stages(payload)
        accepted = {row['key']: row for row in result['accepted']}
        findings = {}
        for item in result['rejected']:
            if item.get('key'):
                findings.setdefault(item['key'], []).append(item)
        keys = sorted(set(accepted) | set(findings))
        if not keys:
            raise DataError('CORRECTION_EVIDENCE', '补正来源没有可识别的业务键')
        digest = sha256(payload).hexdigest()
        touched = []
        for key in keys:
            if key not in baseline:
                raise DataError('CORRECTION_KEY_UNKNOWN', f'原批没有业务键 {key}，无法登记补正')
            base = baseline[key]
            record_row, issues = accepted.get(key), findings.get(key, [])
            evidence = json.dumps({'source_sha256': digest, 'encoding': result['intake']['encoding'],
                                   'record': record_row, 'findings': issues}, ensure_ascii=False)
            if record_row is not None:
                revision = record_row['revision']
                if revision <= base['revision']:
                    raise DataError('CORRECTION_REVISION',
                                    f"{key} 的修订 {revision} 未高于原批修订 {base['revision']}")
                revised = Decimal(record_row['amount'])
                delta = revised - Decimal(base['amount'])
                values = {'revised_amount': format(revised, 'f'), 'delta': format(delta, 'f'),
                          'state': 'ready', 'reason': None, 'detail': None}
            else:
                revision = max((issue.get('revision', 0) for issue in issues), default=0)
                if revision and revision <= base['revision']:
                    raise DataError('CORRECTION_REVISION',
                                    f"{key} 的修订 {revision} 未高于原批修订 {base['revision']}")
                first = issues[0]
                values = {'revised_amount': None, 'delta': None,
                          'state': 'pending', 'reason': first['code'], 'detail': first['detail']}
            cid = correction_id(run_id, key, revision)
            existing = store.find(db, run_id, key, revision)
            if existing is None:
                store.insert(db, {'id': cid, 'run_id': run_id, 'key': key, 'revision': revision,
                                  'origin_revision': base['revision'], 'origin_amount': base['amount'],
                                  **values, 'evidence': evidence, 'signed_version': version,
                                  'created': moment, 'applied_run': None, 'applied': None,
                                  'carried': None})
            elif existing['state'] == 'pending':
                if values['state'] == 'ready':
                    store.mark_ready(db, cid, values['revised_amount'], values['delta'], evidence)
                else:
                    store.mark_pending(db, cid, values['reason'], values['detail'], evidence)
            if values['state'] == 'ready':
                store.supersede_lower(db, run_id, key, revision)
            touched.append(cid)
        return [store.record(store.get(db, cid)) for cid in touched]


def settle(db, run_id, result, moment):
    """把全部待结转补正一次性结转到新批次。生效额只取差额并扣减同键
    此前已结转部分；新批次来源已含该修订时记为吸收，避免重复计入。"""
    accepted = {row['key']: row for row in result['accepted']}
    lines, total = [], Decimal(0)
    for row in store.claim(db, run_id, moment):
        current = accepted.get(row['key'])
        absorbed = current is not None and current['revision'] >= row['revision']
        prior = sum((Decimal(value) for value in
                     store.carried_before(db, row['run_id'], row['key'], row['id'])), Decimal(0))
        effective = Decimal(0) if absorbed else Decimal(row['delta']) - prior
        store.set_carried(db, row['id'], format(effective, 'f'))
        total += effective
        lines.append({'correction': row['id'], 'key': row['key'], 'origin_run': row['run_id'],
                      'origin_revision': row['origin_revision'], 'revision': row['revision'],
                      'origin_amount': row['origin_amount'], 'revised_amount': row['revised_amount'],
                      'delta': row['delta'], 'previously_carried': format(prior, 'f'),
                      'status': 'absorbed' if absorbed else 'applied',
                      'effective': format(effective, 'f')})
    return {'lines': lines, 'total': format(total, 'f'),
            'settled': format(Decimal(result['book']['delta']) + total, 'f')}


def board():
    return store.board()


def for_origin(run_id):
    return store.for_origin(run_id)


def detail(cid):
    return store.detail(cid)
