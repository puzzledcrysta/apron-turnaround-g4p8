from decimal import Decimal
from app.modules.reconcile.service import compare


def _book_index(base_run):
    """外部账按保障单索引；缺键为 None，真实零值保留 Decimal(0)。"""
    return {row['key']: row for row in base_run['book']['rows']}


def build_adjustments(base_run, candidate_run):
    """对照原确认批次与候选批次，逐单产出晚到修订影响明细。"""
    base = {row['key']: row for row in base_run['accepted']}
    candidate = {row['key']: row for row in candidate_run['accepted']}
    book_rows = _book_index(base_run)
    items = []
    for key in sorted(candidate):
        new_row = candidate[key]
        old_row = base.get(key)
        if old_row is None:
            base_revision, base_amount, base_line = 0, None, None
        else:
            base_revision, base_amount, base_line = old_row['revision'], old_row['amount'], old_row['line']
        if old_row is not None and new_row['revision'] <= base_revision:
            continue  # 不是高修订，不构成晚到修订
        new_amount = Decimal(new_row['amount'])
        old_value = Decimal(base_amount) if base_amount is not None else Decimal(0)
        minute_delta = new_amount - old_value
        book_row = book_rows.get(key)
        book_amount = Decimal(book_row['reference']) if book_row and book_row['reference'] is not None else None
        old_book_delta = (old_value - book_amount) if book_amount is not None else None
        new_book_delta = (new_amount - book_amount) if book_amount is not None else None
        if old_book_delta is None:
            external_shift = None
        else:
            external_shift = new_book_delta - old_book_delta
        items.append({
            'key': key,
            'base_revision': base_revision,
            'candidate_revision': new_row['revision'],
            'base_amount': str(old_value) if old_row is not None else None,
            'new_amount': str(new_amount),
            'minute_delta': str(minute_delta),
            'book_amount': str(book_amount) if book_amount is not None else None,
            'book_delta_before': str(old_book_delta) if old_book_delta is not None else None,
            'book_delta_after': str(new_book_delta) if book_amount is not None else None,
            'book_delta_shift': str(external_shift) if external_shift is not None else None,
            'base_line': base_line,
            'new_line': new_row['line'],
            'kind': 'NEW_KEY' if old_row is None else 'REVISION',
        })
    return items


def build_result(base_run, included):
    """以原确认账单为底，仅按纳入的调整替换对应保障单，重新独立对账。"""
    rows = [dict(row) for row in base_run['accepted']]
    by_key = {row['key']: index for index, row in enumerate(rows)}
    applied = []
    for item in included:
        replacement = {
            'key': item['key'], 'line': item['new_line'], 'revision': item['candidate_revision'],
            'amount': item['new_amount'], 'unit': '计费分钟',
            'raw': [], 'proof': [], 'settled_from': item['id'],
        }
        if item['key'] in by_key:
            rows[by_key[item['key']]] = replacement
        else:
            by_key[item['key']] = len(rows)
            rows.append(replacement)
        applied.append({'adjustment_id': item['id'], 'key': item['key'],
                        'from_revision': item['base_revision'], 'to_revision': item['candidate_revision'],
                        'minute_delta': item['minute_delta']})
    rows.sort(key=lambda row: row['key'])
    return rows, applied


def settle(base_run, included, book_bytes):
    rows, applied = build_result(base_run, included)
    book = compare(rows, book_bytes)
    return {
        'basis_run_id': base_run['manifest']['run_id'],
        'included_adjustments': applied,
        'results': rows,
        'reconciliation': book,
        'totals': {
            'billed_minutes': book['actual'],
            'external_minutes': book['expected'],
            'delta': book['delta'],
            'mismatched': book['mismatched'],
        },
    }
