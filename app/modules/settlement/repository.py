import json
from app.errors import DataError
from app.store import connection


def get_settlement(db, settlement_id):
    row = db.execute('SELECT * FROM settlements WHERE id=?', (settlement_id,)).fetchone()
    if row is None:
        raise DataError('SETTLEMENT_UNKNOWN', '没有此关账单', 404)
    return row


def create_settlement(db, settlement_id, base_run_id, created):
    db.execute('INSERT INTO settlements(id,base_run_id,status,created,closed) VALUES(?,?,?,?,?)',
               (settlement_id, base_run_id, 'open', created, None))
    return get_settlement(db, settlement_id)


def list_adjustments(db, settlement_id):
    return [dict(row) for row in db.execute(
        'SELECT * FROM adjustments WHERE settlement_id=? ORDER BY key,candidate_revision', (settlement_id,))]


def find_adjustment(db, settlement_id, key, revision):
    return db.execute(
        'SELECT * FROM adjustments WHERE settlement_id=? AND key=? AND candidate_revision=?',
        (settlement_id, key, revision)).fetchone()


def upsert_adjustment(db, item):
    existing = find_adjustment(db, item['settlement_id'], item['key'], item['candidate_revision'])
    if existing is not None:
        legacy = {k: existing[k] for k in
                  ('candidate_run_id', 'base_amount', 'new_amount', 'minute_delta', 'book_amount',
                   'book_delta', 'base_line', 'new_line', 'kind')}
        current = {k: item[k] for k in
                   ('candidate_run_id', 'base_amount', 'new_amount', 'minute_delta', 'book_amount',
                    'book_delta', 'base_line', 'new_line', 'kind')}
        if legacy != current:
            raise DataError('ADJUST_CONFLICT',
                            f'保障单 {item["key"]} 修订 {item["candidate_revision"]} 的重算结果与已登记内容不一致', 409)
        return dict(existing), False
    db.execute(
        '''INSERT INTO adjustments(settlement_id,key,base_revision,candidate_revision,candidate_run_id,
           status,base_amount,new_amount,minute_delta,book_amount,book_delta,base_line,new_line,kind,
           note,changed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (item['settlement_id'], item['key'], item['base_revision'], item['candidate_revision'],
         item['candidate_run_id'], item['status'], item['base_amount'], item['new_amount'],
         item['minute_delta'], item['book_amount'], item['book_delta'], item['base_line'],
         item['new_line'], item['kind'], item['note'], item['changed']))
    # 同一保障单只有最新修订可以保留为候选，旧候选留痕但不再参与任何结算
    db.execute(
        '''UPDATE adjustments SET status='superseded' WHERE settlement_id=? AND key=?
           AND candidate_revision<? AND status IN ('pending','included','deferred')''',
        (item['settlement_id'], item['key'], item['candidate_revision']))
    return dict(find_adjustment(db, item['settlement_id'], item['key'], item['candidate_revision'])), True


def decide_adjustment(db, adjustment_id, status, note, changed):
    row = db.execute('SELECT * FROM adjustments WHERE id=?', (adjustment_id,)).fetchone()
    if row is None:
        raise DataError('ADJUST_UNKNOWN', '没有此调整记录', 404)
    db.execute('UPDATE adjustments SET status=?,note=?,changed=? WHERE id=?',
               (status, note, changed, adjustment_id))
    return dict(db.execute('SELECT * FROM adjustments WHERE id=?', (adjustment_id,)).fetchone())


def save_version(db, settlement_id, version, snapshot, created):
    db.execute('INSERT INTO settlement_versions VALUES(?,?,?,?)',
               (settlement_id, version, json.dumps(snapshot, ensure_ascii=False), created))


def latest_version(db, settlement_id):
    return db.execute(
        'SELECT COALESCE(MAX(version),0) FROM settlement_versions WHERE settlement_id=?',
        (settlement_id,)).fetchone()[0]


def get_version(db, settlement_id, version):
    row = db.execute(
        'SELECT * FROM settlement_versions WHERE settlement_id=? AND version=?',
        (settlement_id, version)).fetchone()
    if row is None:
        raise DataError('VERSION_UNKNOWN', '没有此结算版本', 404)
    return row


def close_settlement(db, settlement_id, closed):
    db.execute('UPDATE settlements SET status=?,closed=? WHERE id=?',
               ('closed', closed, settlement_id))


def list_settlements():
    with connection() as db:
        return [dict(row) for row in db.execute(
            'SELECT id,base_run_id,status,created,closed FROM settlements ORDER BY created DESC,id DESC LIMIT 50')]
