import csv
from io import StringIO
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
HEADER = '保障单,修订号,提交时间,起分钟,止分钟,豁免窗口,每单位时长,原始备注'


def _csv(rows):
    out = StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(HEADER.split(','))
    writer.writerows(rows)
    return out.getvalue()


@pytest.fixture
def confirmed(client):
    """已确认的原批次（即原确认账单）。"""
    run_id = client.post('/api/runs', json={}).json()['manifest']['run_id']
    response = client.post(f'/api/review/{run_id}',
                           json={'version': 0, 'decision': '确认', 'note': '月度关账前确认'})
    assert response.status_code == 200
    return run_id


@pytest.fixture
def settlement(client, confirmed):
    return client.post('/api/settlements', json={'base_run_id': confirmed}).json()['id']


def _late_source(revision=2, start=500, end=550):
    # 00101 原确认：480-551，豁免并集 490:520 → 净 40 分钟 → 计费 45
    return _csv([
        ['00101', revision, '2026-11-19T09:30:00+08:00', start, end, '无', 15, '晚到高修订'],
    ])


def test_settlement_requires_confirmed_base(client):
    run_id = client.post('/api/runs', json={}).json()['manifest']['run_id']
    response = client.post('/api/settlements', json={'base_run_id': run_id})
    assert (response.status_code, response.json()['code']) == (422, 'BASE_NOT_CONFIRMED')


def test_unknown_settlement_404(client):
    response = client.get('/api/settlements/nope')
    assert (response.status_code, response.json()['code']) == (404, 'SETTLEMENT_UNKNOWN')


def test_preview_shows_minute_and_external_deltas(client, settlement):
    report = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': _late_source()}).json()
    assert report['late_revisions'] == 1
    adjustment = report['registered'][0]['adjustment']
    assert adjustment['key'] == '00101'
    assert (adjustment['base_revision'], adjustment['candidate_revision']) == (1, 2)
    # 原 45 分钟 → 新 60 分钟（50 分钟按 15 进位）；外部账 00101=45，外部差由 0 变为 +15
    assert adjustment['base_amount'] == '45' and adjustment['new_amount'] == '60'
    assert adjustment['minute_delta'] == '15'
    assert (adjustment['book_amount'], adjustment['book_delta_before'],
            adjustment['book_delta_after'], adjustment['book_delta_shift']) == ('45', '0', '15', '15')
    assert adjustment['status'] == 'pending'


def test_rerun_same_revision_is_idempotent(client, settlement):
    payload = {'data': _late_source()}
    first = client.post(f'/api/settlements/{settlement}/adjustments', json=payload).json()
    second = client.post(f'/api/settlements/{settlement}/adjustments', json=payload).json()
    assert first['registered'][0]['adjustment']['id'] == second['registered'][0]['adjustment']['id']
    assert second['registered'][0]['inserted'] is False
    detail = client.get(f'/api/settlements/{settlement}').json()
    assert detail['adjustment_counts']['total'] == 1


def test_rerun_with_changed_payload_conflicts_and_keeps_old(client, settlement):
    client.post(f'/api/settlements/{settlement}/adjustments', json={'data': _late_source()})
    conflict = client.post(f'/api/settlements/{settlement}/adjustments',
                           json={'data': _late_source(end=530)})
    assert (conflict.status_code, conflict.json()['code']) == (409, 'ADJUST_CONFLICT')
    detail = client.get(f'/api/settlements/{settlement}').json()
    only = detail['adjustments'][0]
    assert only['new_amount'] == '60'  # 旧版调整保留，未被静默改写


def test_lower_revision_is_not_a_late_revision(client, settlement):
    report = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': _late_source(revision=1)}).json()
    assert report['late_revisions'] == 0 and report['registered'] == []


def test_decision_requires_note(client, settlement):
    report = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': _late_source()}).json()
    adjustment_id = report['registered'][0]['adjustment']['id']
    response = client.post(f'/api/settlements/{settlement}/adjustments/{adjustment_id}/decision',
                           json={'decision': '纳入', 'note': '  '})
    assert (response.status_code, response.json()['code']) == (422, 'REVIEW_NOTE')


def test_pending_adjustment_excluded_from_result(client, settlement):
    client.post(f'/api/settlements/{settlement}/adjustments', json={'data': _late_source()})
    snapshot = client.post(f'/api/settlements/{settlement}/versions').json()
    row = next(row for row in snapshot['results'] if row['key'] == '00101')
    assert row['amount'] == '45' and row['revision'] == 1  # 未确认，保持原确认账单
    assert len(snapshot['pending']) == 1 and snapshot['included_adjustments'] == []


def test_included_adjustment_rebuilds_result_and_reconciles(client, settlement):
    report = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': _late_source()}).json()
    adjustment_id = report['registered'][0]['adjustment']['id']
    decided = client.post(f'/api/settlements/{settlement}/adjustments/{adjustment_id}/decision',
                          json={'decision': '纳入', 'note': '复核无误，本期纳入'})
    assert decided.status_code == 200 and decided.json()['status'] == 'included'
    snapshot = client.post(f'/api/settlements/{settlement}/versions').json()
    row = next(row for row in snapshot['results'] if row['key'] == '00101')
    assert (row['amount'], row['revision']) == ('60', 2)
    assert row['settled_from'] == adjustment_id
    assert snapshot['totals']['billed_minutes'] == snapshot['reconciliation']['actual']
    # 外部账差值跟随变化：00101 由相符变为 +15
    book_row = next(row for row in snapshot['reconciliation']['rows'] if row['key'] == '00101')
    assert (book_row['amount'], book_row['reference'], book_row['delta']) == ('60', '45', '15')


def test_deferred_adjustment_keeps_base_and_is_listed(client, settlement):
    report = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': _late_source()}).json()
    adjustment_id = report['registered'][0]['adjustment']['id']
    client.post(f'/api/settlements/{settlement}/adjustments/{adjustment_id}/decision',
                json={'decision': '留待下期', 'note': '依据不足，下期处理'})
    snapshot = client.post(f'/api/settlements/{settlement}/versions').json()
    row = next(row for row in snapshot['results'] if row['key'] == '00101')
    assert (row['amount'], row['revision']) == ('45', 1)
    assert snapshot['deferred'][0]['id'] == adjustment_id
    assert snapshot['pending'] == []


def test_close_blocked_with_pending_adjustment(client, settlement):
    client.post(f'/api/settlements/{settlement}/adjustments', json={'data': _late_source()})
    client.post(f'/api/settlements/{settlement}/versions')
    response = client.post(f'/api/settlements/{settlement}/close')
    assert (response.status_code, response.json()['code']) == (422, 'PENDING_ADJUSTMENTS')


def test_close_without_version_blocked(client, settlement):
    response = client.post(f'/api/settlements/{settlement}/close')
    assert (response.status_code, response.json()['code']) == (422, 'NO_SETTLEMENT')


def test_close_freezes_result_and_blocks_writes(client, settlement):
    report = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': _late_source()}).json()
    adjustment_id = report['registered'][0]['adjustment']['id']
    client.post(f'/api/settlements/{settlement}/adjustments/{adjustment_id}/decision',
                json={'decision': '纳入', 'note': '本期纳入'})
    client.post(f'/api/settlements/{settlement}/versions')
    closed = client.post(f'/api/settlements/{settlement}/close')
    assert closed.status_code == 200 and closed.json()['status'] == 'closed'
    again = client.post(f'/api/settlements/{settlement}/close')
    assert (again.status_code, again.json()['code']) == (409, 'SETTLEMENT_CLOSED')
    register = client.post(f'/api/settlements/{settlement}/adjustments',
                           json={'data': _late_source(revision=3)})
    assert (register.status_code, register.json()['code']) == (409, 'SETTLEMENT_CLOSED')
    decide = client.post(f'/api/settlements/{settlement}/adjustments/{adjustment_id}/decision',
                         json={'decision': '留待下期', 'note': '封账后尝试改写'})
    assert (decide.status_code, decide.json()['code']) == (409, 'SETTLEMENT_CLOSED')


def test_base_run_remains_untouched(client, confirmed, settlement):
    client.post(f'/api/settlements/{settlement}/adjustments', json={'data': _late_source()})
    base = client.get(f'/api/runs/{confirmed}').json()
    row = next(row for row in base['accepted'] if row['key'] == '00101')
    assert (row['revision'], row['amount']) == (1, '45')


def test_new_key_adjustment_book_delta(client, settlement):
    source = _csv([['00777', 1, '2026-11-19T09:30:00+08:00', 10, 20, '无', 15, '新增保障单']])
    report = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': source}).json()
    adjustment = report['registered'][0]['adjustment']
    assert adjustment['kind'] == 'NEW_KEY'
    assert (adjustment['base_amount'], adjustment['new_amount'], adjustment['minute_delta']) == (None, '15', '15')
    assert adjustment['book_amount'] is None  # 外部账无此键，外部差不可量化但不阻断


def test_higher_revision_supersedes_pending_candidate(client, settlement):
    first = client.post(f'/api/settlements/{settlement}/adjustments',
                        json={'data': _late_source(revision=2, end=550)}).json()
    second = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': _late_source(revision=3, end=530)}).json()
    old_id = first['registered'][0]['adjustment']['id']
    new_id = second['registered'][0]['adjustment']['id']
    detail = client.get(f'/api/settlements/{settlement}').json()
    statuses = {row['id']: row['status'] for row in detail['adjustments']}
    assert statuses[old_id] == 'superseded' and statuses[new_id] == 'pending'


def test_export_contains_settlement_and_history(client, settlement):
    report = client.post(f'/api/settlements/{settlement}/adjustments',
                         json={'data': _late_source()}).json()
    adjustment_id = report['registered'][0]['adjustment']['id']
    client.post(f'/api/settlements/{settlement}/adjustments/{adjustment_id}/decision',
                json={'decision': '纳入', 'note': '本期纳入'})
    client.post(f'/api/settlements/{settlement}/versions')
    response = client.get(f'/api/settlements/{settlement}/export')
    assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
    bundle = response.json()
    assert bundle['settlement_result']['version'] == 1
    assert any(item['id'] == adjustment_id for item in bundle['adjustments'])
    assert bundle['base_totals']['billed_minutes']


def test_export_without_version_is_rejected(client, settlement):
    response = client.get(f'/api/settlements/{settlement}/export')
    assert (response.status_code, response.json()['code']) == (422, 'NO_SETTLEMENT')


def test_failed_candidate_run_changes_nothing(client, settlement):
    before = client.get(f'/api/settlements/{settlement}').json()['adjustment_counts']
    response = client.post(f'/api/settlements/{settlement}/adjustments',
                           json={'data': 'bad\na'})
    assert response.status_code == 422
    after = client.get(f'/api/settlements/{settlement}').json()['adjustment_counts']
    assert before == after


def test_concurrent_registration_of_same_revision_accumulates_once(client, settlement):
    from concurrent.futures import ThreadPoolExecutor
    from app.modules.settlement.service import register
    from app.settings import SAMPLE_ROOT

    source = _late_source().encode()
    book = (SAMPLE_ROOT / 'external.csv').read_bytes()
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: register(settlement, source, book), range(4)))
    assert all(report['late_revisions'] == 1 for report in outcomes)
    detail = client.get(f'/api/settlements/{settlement}').json()
    assert detail['adjustment_counts']['total'] == 1
