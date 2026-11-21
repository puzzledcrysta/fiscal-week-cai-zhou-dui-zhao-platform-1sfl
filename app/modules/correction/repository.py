import json
from app.errors import DataError
from app.store import connection

COLUMNS = ('id','run_id','key','revision','origin_revision','origin_amount','revised_amount',
           'delta','state','reason','detail','evidence','signed_version','created',
           'applied_run','applied','carried')
SELECT = f"SELECT {','.join(COLUMNS)} FROM corrections"


def record(row):
    item = dict(row)
    item['evidence'] = json.loads(item['evidence'])
    return item


def get_run(db, run_id):
    row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    if row is None:
        raise DataError('RUN_UNKNOWN', '没有此批次', 404)
    return row


def signed_version(db, run_id):
    row = db.execute('SELECT version,decision FROM reviews WHERE run_id=? '
                     'ORDER BY version DESC LIMIT 1', (run_id,)).fetchone()
    if row is None or row['decision'] != '确认':
        raise DataError('RUN_UNSIGNED', '月结未签字确认，不能登记补正', 409)
    return row['version']


def get(db, cid):
    return db.execute(f'{SELECT} WHERE id=?', (cid,)).fetchone()


def find(db, run_id, key, revision):
    return db.execute(f'{SELECT} WHERE run_id=? AND key=? AND revision=?',
                      (run_id, key, revision)).fetchone()


def insert(db, values):
    slots = ','.join('?' for _ in COLUMNS)
    db.execute(f'INSERT OR IGNORE INTO corrections ({",".join(COLUMNS)}) VALUES ({slots})',
               tuple(values[column] for column in COLUMNS))


def mark_ready(db, cid, revised, delta, evidence):
    db.execute("UPDATE corrections SET state='ready',revised_amount=?,delta=?,"
               'reason=NULL,detail=NULL,evidence=? WHERE id=?', (revised, delta, evidence, cid))


def mark_pending(db, cid, reason, detail, evidence):
    db.execute("UPDATE corrections SET state='pending',reason=?,detail=?,evidence=? WHERE id=?",
               (reason, detail, evidence, cid))


def supersede_lower(db, run_id, key, revision):
    db.execute("UPDATE corrections SET state='superseded' "
               "WHERE run_id=? AND key=? AND revision<? AND state='pending'",
               (run_id, key, revision))


def claim(db, run_id, moment):
    rows = db.execute("UPDATE corrections SET state='applied',applied_run=?,applied=? "
                      "WHERE state='ready' RETURNING *", (run_id, moment)).fetchall()
    return sorted(rows, key=lambda row: (row['key'], row['revision']))


def carried_before(db, run_id, key, exclude):
    rows = db.execute("SELECT carried FROM corrections WHERE run_id=? AND key=? "
                      "AND state='applied' AND carried IS NOT NULL AND id<>?",
                      (run_id, key, exclude)).fetchall()
    return [row['carried'] for row in rows]


def set_carried(db, cid, amount):
    db.execute('UPDATE corrections SET carried=? WHERE id=?', (amount, cid))


def board():
    with connection() as db:
        return [record(row) for row in db.execute(f'{SELECT} ORDER BY created DESC,id DESC')]


def for_origin(run_id):
    with connection() as db:
        get_run(db, run_id)
        return [record(row) for row in
                db.execute(f'{SELECT} WHERE run_id=? ORDER BY key,revision', (run_id,))]


def detail(cid):
    with connection() as db:
        row = get(db, cid)
        if row is None:
            raise DataError('CORRECTION_UNKNOWN', '没有此补正', 404)
        return record(row)
