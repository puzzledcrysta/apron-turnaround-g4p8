import json
import uuid
from datetime import datetime, timezone
from app.errors import DataError
from app.store import connection
from app.modules.runs.repository import lookup
from app.modules.runs.service import run as create_run
from . import repository as repo
from .results import build_adjustments, settle

DECISION_STATUS = {'纳入': 'included', '留待下期': 'deferred'}
ACTIVE_STATUS = ('pending', 'included', 'deferred')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _latest_review_confirmed(db, run_id):
    row = db.execute(
        'SELECT decision FROM reviews WHERE run_id=? ORDER BY version DESC LIMIT 1', (run_id,)).fetchone()
    return row is not None and row['decision'] == '确认'


def _enrich(row):
    """补齐外部账差值的前后视图，供复核工作台展示晚到修订的影响。"""
    item = dict(row)
    item.pop('settlement_id', None)
    if row['book_amount'] is None or row['book_delta'] is None:
        item.update(book_delta_before=None, book_delta_after=None, book_delta_shift=None)
    else:
        from decimal import Decimal
        after = Decimal(row['book_delta'])
        shift = Decimal(row['minute_delta'])
        item.update(book_delta_after=str(after),
                    book_delta_before=str(after - shift),
                    book_delta_shift=str(shift))
    return item


def _serialize_adjustment(row):
    return _enrich(row)


def create(base_run_id):
    """关账调整只能挂在已确认的原批次上；原批次结果保持只读。"""
    created = _now()
    settlement_id = uuid.uuid4().hex
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        lookup(db, base_run_id)
        if not _latest_review_confirmed(db, base_run_id):
            raise DataError('BASE_NOT_CONFIRMED', '原批次尚未确认，不能创建关账调整', 422)
        db.execute('INSERT INTO settlements(id,base_run_id,status,created,closed) VALUES(?,?,?,?,?)',
                   (settlement_id, base_run_id, 'open', created, None))
        return dict(repo.get_settlement(db, settlement_id))


def register(settlement_id, source, book):
    """登记一次晚到修订批次；同一修订重跑幂等，内容冲突则拒绝并保留旧记录。"""
    changed = _now()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        settlement = repo.get_settlement(db, settlement_id)
        if settlement['status'] != 'open':
            raise DataError('SETTLEMENT_CLOSED', '关账单已封账，不能再登记修订', 409)
        base_run = json.loads(lookup(db, settlement['base_run_id'])['result'])
    # 复用批次复算（INSERT OR IGNORE 幂等），失败时整个登记不产生任何改动
    candidate_run = create_run(source, book)
    candidate_id = candidate_run['manifest']['run_id']
    diffs = build_adjustments(base_run, candidate_run)
    registered = []
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        # 封账状态可能在复算期间变化，二次确认
        if repo.get_settlement(db, settlement_id)['status'] != 'open':
            raise DataError('SETTLEMENT_CLOSED', '关账单已封账，不能再登记修订', 409)
        for diff in diffs:
            item = {
                'settlement_id': settlement_id,
                'key': diff['key'],
                'base_revision': diff['base_revision'],
                'candidate_revision': diff['candidate_revision'],
                'candidate_run_id': candidate_id,
                'status': 'pending',
                'base_amount': diff['base_amount'],
                'new_amount': diff['new_amount'],
                'minute_delta': diff['minute_delta'],
                'book_amount': diff['book_amount'],
                'book_delta': diff['book_delta_after'],
                'base_line': diff['base_line'],
                'new_line': diff['new_line'],
                'kind': diff['kind'],
                'note': '',
                'changed': changed,
            }
            row, inserted = repo.upsert_adjustment(db, item)
            stored = _serialize_adjustment(row)
            stored.update(book_delta_before=diff['book_delta_before'],
                          book_delta_after=diff['book_delta_after'],
                          book_delta_shift=diff['book_delta_shift'])
            registered.append({'adjustment': stored, 'inserted': inserted})
    return {'candidate_run_id': candidate_id, 'late_revisions': len(diffs), 'registered': registered}


def decide(settlement_id, adjustment_id, decision, note):
    note = note.strip()
    if decision not in DECISION_STATUS:
        raise DataError('REVIEW_DECISION', '调整结论无效')
    if not note:
        raise DataError('REVIEW_NOTE', '纳入或留待下期均须填写复核意见')
    changed = _now()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        settlement = repo.get_settlement(db, settlement_id)
        if settlement['status'] != 'open':
            raise DataError('SETTLEMENT_CLOSED', '关账单已封账，不能再修改调整结论', 409)
        row = db.execute('SELECT * FROM adjustments WHERE id=? AND settlement_id=?',
                         (adjustment_id, settlement_id)).fetchone()
        if row is None:
            raise DataError('ADJUST_UNKNOWN', '没有此调整记录', 404)
        updated = repo.decide_adjustment(db, adjustment_id, DECISION_STATUS[decision], note, changed)
        return _serialize_adjustment(updated)


def _active_adjustments(db, settlement_id):
    rows = db.execute(
        'SELECT * FROM adjustments WHERE settlement_id=? AND status IN (?,?,?) ORDER BY key',
        (settlement_id, *ACTIVE_STATUS)).fetchall()
    latest = {}
    for row in rows:  # 同一 key 只保留候选修订最高的一条
        if row['key'] not in latest or row['candidate_revision'] > latest[row['key']]['candidate_revision']:
            latest[row['key']] = dict(row)
    return list(latest.values())


def produce(settlement_id):
    """生成新结算结果版本；未确认（pending）的调整不进入正式结果。失败不影响既有版本。"""
    created = _now()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        settlement = repo.get_settlement(db, settlement_id)
        if settlement['status'] != 'open':
            raise DataError('SETTLEMENT_CLOSED', '关账单已封账，结算版本已固定', 409)
        base_stored = lookup(db, settlement['base_run_id'])
        base_run = json.loads(base_stored['result'])
        active = _active_adjustments(db, settlement_id)
        included = [row for row in active if row['status'] == 'included']
        snapshot = settle(base_run, included, base_stored['book'])
        pending = [row for row in active if row['status'] == 'pending']
        deferred = [row for row in active if row['status'] == 'deferred']
        snapshot.update({
            'settlement_id': settlement_id,
            'status': 'open',
            'generated': created,
            'pending': [_serialize_adjustment(row) for row in pending],
            'deferred': [_serialize_adjustment(row) for row in deferred],
        })
        version = repo.latest_version(db, settlement_id) + 1
        snapshot['version'] = version
        repo.save_version(db, settlement_id, version, snapshot, created)
        return snapshot


def close(settlement_id):
    """封账：仍有未确认调整时拒绝；封账后正式结果固定为最新版本。"""
    closed = _now()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        settlement = repo.get_settlement(db, settlement_id)
        if settlement['status'] == 'closed':
            raise DataError('SETTLEMENT_CLOSED', '关账单已封账', 409)
        pending = db.execute(
            "SELECT COUNT(*) FROM adjustments WHERE settlement_id=? AND status='pending'",
            (settlement_id,)).fetchone()[0]
        if pending:
            raise DataError('PENDING_ADJUSTMENTS', f'仍有 {pending} 条调整未确认，不能封账', 422)
        if repo.latest_version(db, settlement_id) == 0:
            raise DataError('NO_SETTLEMENT', '尚未生成结算结果，不能封账', 422)
        repo.close_settlement(db, settlement_id, closed)
        version = repo.latest_version(db, settlement_id)
        row = repo.get_version(db, settlement_id, version)
        snapshot = json.loads(row['snapshot'])
        snapshot['status'] = 'closed'
        snapshot['closed'] = closed
        db.execute('UPDATE settlement_versions SET snapshot=? WHERE settlement_id=? AND version=?',
                   (json.dumps(snapshot, ensure_ascii=False), settlement_id, version))
        return snapshot


def detail(settlement_id):
    with connection() as db:
        settlement = dict(repo.get_settlement(db, settlement_id))
        base_run = json.loads(lookup(db, settlement['base_run_id'])['result'])
        adjustments = [_serialize_adjustment(row) for row in repo.list_adjustments(db, settlement_id)]
        version_no = repo.latest_version(db, settlement_id)
        latest = None
        if version_no:
            latest = json.loads(repo.get_version(db, settlement_id, version_no)['snapshot'])
        counts = {'total': len(adjustments),
                  'pending': sum(1 for row in adjustments if row['status'] == 'pending'),
                  'included': sum(1 for row in adjustments if row['status'] == 'included'),
                  'deferred': sum(1 for row in adjustments if row['status'] == 'deferred'),
                  'superseded': sum(1 for row in adjustments if row['status'] == 'superseded')}
        return {**settlement, 'base_manifest': base_run['manifest'],
                'adjustments': adjustments, 'adjustment_counts': counts,
                'latest_version': latest, 'latest_version_no': version_no}


def version(settlement_id, version_no):
    with connection() as db:
        repo.get_settlement(db, settlement_id)
        return json.loads(repo.get_version(db, settlement_id, version_no)['snapshot'])


def export_bundle(settlement_id):
    with connection() as db:
        settlement = repo.get_settlement(db, settlement_id)
        version_no = repo.latest_version(db, settlement_id)
        if version_no == 0:
            raise DataError('NO_SETTLEMENT', '尚未生成结算结果，无可导出内容', 422)
        snapshot = json.loads(repo.get_version(db, settlement_id, version_no)['snapshot'])
        base = json.loads(lookup(db, settlement['base_run_id'])['result'])
        adjustments = [_serialize_adjustment(row) for row in repo.list_adjustments(db, settlement_id)]
    return {'settlement': {'id': settlement['id'], 'base_run_id': settlement['base_run_id'],
                           'status': settlement['status'], 'created': settlement['created'],
                           'closed': settlement['closed']},
            'base_manifest': base['manifest'],
            'base_totals': {'billed_minutes': base['book']['actual'],
                            'external_minutes': base['book']['expected'],
                            'delta': base['book']['delta'], 'mismatched': base['book']['mismatched']},
            'adjustments': adjustments,
            'settlement_version': snapshot,
            'settlement_result': snapshot}


def list_all():
    return repo.list_settlements()
