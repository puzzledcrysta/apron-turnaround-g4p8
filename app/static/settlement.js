import {query, encodeInput} from './api.js';
import {el, table} from './table.js';

// 关账调整工作台：原确认账单只读，晚到修订先预览影响，再由复核员逐单裁决。
const STATUS_LABEL = {pending: '待确认', included: '纳入本次', deferred: '留待下期', superseded: '已被新修订取代'};
const KIND_LABEL = {REVISION: '高修订', NEW_KEY: '新增保障单'};

let currentSettlement = null;

function button(text, handler, primary = false) {
  const node = el('button', text, primary ? 'btn btn--primary' : 'btn');
  node.type = 'button';
  node.addEventListener('click', handler);
  return node;
}

function section(title) {
  const wrap = el('div', undefined, 'settle-section');
  wrap.append(el('h3', title));
  return wrap;
}

function summary(detail) {
  const wrap = section('关账单');
  const counts = detail.adjustment_counts;
  const meta = el('p', undefined, 'settle-meta');
  meta.append(
    el('span', `状态：${detail.status === 'closed' ? '已封账' : '开放中'}`),
    el('span', `原批次：${detail.base_run_id.slice(0, 9)}`),
    el('span', `待确认 ${counts.pending} · 纳入 ${counts.included} · 留待 ${counts.deferred} · 取代 ${counts.superseded}`),
  );
  wrap.append(meta);
  return wrap;
}

function decisionsTable(detail, reload, notify) {
  const wrap = section('晚到修订复核（计费分钟与外部账差异预览）');
  const rows = detail.adjustments.filter(item => item.status !== 'superseded');
  if (!rows.length) {
    wrap.append(el('p', '尚未登记晚到修订批次', 'empty'));
    return wrap;
  }
  const view = [
    ['保障单', 'key'],
    ['类型', row => KIND_LABEL[row.kind] || row.kind],
    ['原修订', 'base_revision', 'num'],
    ['新修订', 'candidate_revision', 'num'],
    ['原计费分钟', row => row.base_amount ?? '—', 'num'],
    ['新计费分钟', 'new_amount', 'num'],
    ['分钟差', 'minute_delta', 'num'],
    ['外部账', row => row.book_amount ?? '无此键', 'num'],
    ['外部差(前)', row => row.book_delta_before ?? '—', 'num'],
    ['外部差(后)', row => row.book_delta_after ?? '—', 'num'],
    ['结论', row => el('span', STATUS_LABEL[row.status] || row.status,
                       row.status === 'pending' ? 'state problem' : 'state')],
    ['操作', row => actionCell(row, detail, reload, notify)],
  ];
  table(wrap, view, rows);
  return wrap;
}

function actionCell(row, detail, reload, notify) {
  const cell = el('div', undefined, 'settle-actions');
  if (detail.status === 'closed' || row.status === 'superseded') {
    cell.append(el('span', '已固定', 'mute'));
    return cell;
  }
  const note = el('textarea');
  note.maxLength = 500;
  note.placeholder = '复核意见（必填）';
  note.value = row.note || '';
  const decide = async decision => {
    if (!note.value.trim()) {
      notify('纳入或留待下期均须填写复核意见', true);
      return;
    }
    try {
      await query(`/api/settlements/${currentSettlement}/adjustments/${row.id}/decision`,
                  {decision, note: note.value});
      notify('调整结论已登记');
      await reload();
    } catch (error) { notify(error.message, true); }
  };
  cell.append(note,
              button('纳入本次', () => decide('纳入'), true),
              button('留待下期', () => decide('留待下期')));
  return cell;
}

function latestVersion(detail, notify, reload) {
  const wrap = section('最新结算结果');
  const snapshot = detail.latest_version;
  if (!snapshot) {
    wrap.append(el('p', '还没有生成过结算版本', 'empty'));
    return wrap;
  }
  const totals = snapshot.totals;
  const meta = el('p', undefined, 'settle-meta');
  meta.append(
    el('span', `版本 v${snapshot.version}（${snapshot.status === 'closed' ? '已封账' : '草稿'}）`),
    el('span', `结算计费分钟：${totals.billed_minutes}`),
    el('span', `外部账：${totals.external_minutes}`),
    el('span', `差异：${totals.delta}`),
    el('span', `待查：${totals.mismatched}`),
  );
  wrap.append(meta);
  const applied = snapshot.included_adjustments || [];
  if (applied.length) {
    table(wrap, [['保障单', 'key'], ['原修订', 'from_revision', 'num'], ['新修订', 'to_revision', 'num'],
                 ['分钟差', 'minute_delta', 'num']], applied);
  } else {
    wrap.append(el('p', '本版本未纳入任何调整，等同原确认账单', 'empty'));
  }
  if (snapshot.pending && snapshot.pending.length) {
    wrap.append(el('p', `另有 ${snapshot.pending.length} 条未确认调整未进入正式结果`, 'notice problem'));
  }
  return wrap;
}

export async function renderSettlement(panel, runId, notify) {
  panel.hidden = false;
  panel.replaceChildren(el('p', '加载中', 'empty'));
  const list = await query('/api/settlements');
  const owned = list.filter(item => item.base_run_id === runId);
  currentSettlement = owned[0]?.id || null;

  panel.replaceChildren();
  if (!currentSettlement) {
    const intro = section('月度关账调整');
    intro.append(el('p', '基于当前已确认的原批次创建关账单。原确认账单保持只读，晚到的高修订保障单在此预览并逐单裁决。', 'empty'));
    intro.append(button('从当前确认批次创建关账单', async () => {
      try {
        const record = await query('/api/settlements', {base_run_id: runId});
        currentSettlement = record.id;
        notify('关账单已创建');
        await refresh();
      } catch (error) { notify(error.message, true); }
    }, true));
    panel.append(intro);
    return;
  }
  await refresh();

  async function refresh() {
    const detail = await query(`/api/settlements/${currentSettlement}`);
    panel.replaceChildren();
    panel.append(summary(detail));
    panel.append(registerBox(detail, notify, refresh));
    panel.append(decisionsTable(detail, refresh, notify));
    panel.append(versionBox(detail, notify, refresh));
    panel.append(latestVersion(detail, notify, refresh));
  }
}

function registerBox(detail, notify, refresh) {
  const wrap = section('登记晚到修订批次');
  if (detail.status === 'closed') {
    wrap.append(el('p', '关账单已封账，不再接收修订', 'empty'));
    return wrap;
  }
  const file = el('input');
  file.type = 'file';
  file.accept = '.csv,text/csv';
  const text = el('textarea');
  text.placeholder = '粘贴含高修订保障单的来源 CSV（外部账缺省沿用原批次外部账）';
  const submit = button('预览并登记修订', submitChanges, true);
  async function submitChanges() {
    submit.disabled = true;
    try {
      const payload = text.value ? {data: text.value} : {};
      if (file.files[0]) {
        const bytes = new Uint8Array(await file.files[0].arrayBuffer());
        payload.data_base64 = encodeInput(bytes);
      }
      const report = await query(`/api/settlements/${currentSettlement}/adjustments`, payload);
      notify(`已登记 ${report.late_revisions} 条晚到修订（重复重跑不会累计）`);
      await refresh();
    } catch (error) { notify(error.message, true); }
    finally { submit.disabled = false; }
  }
  wrap.append(el('label', '修订来源 CSV'), file, text, submit);
  return wrap;
}

function versionBox(detail, notify, refresh) {
  const wrap = section('结算与封账');
  if (detail.status === 'closed') {
    const exportBtn = el('a', '导出正式结算结果', 'btn btn--primary');
    exportBtn.href = `/api/settlements/${currentSettlement}/export`;
    wrap.append(el('p', '正式结果已固定，可导出归档。', 'empty'), exportBtn);
    return wrap;
  }
  wrap.append(
    button('生成新结算版本（未确认调整不进入）', async () => {
      try {
        const snapshot = await query(`/api/settlements/${currentSettlement}/versions`, {});
        notify(`结算版本 v${snapshot.version} 已生成`);
        await refresh();
      } catch (error) { notify(error.message, true); }
    }, true),
    button('封账（须先裁决全部调整）', async () => {
      try {
        await query(`/api/settlements/${currentSettlement}/close`, {});
        notify('已封账，正式结果固定');
        await refresh();
      } catch (error) { notify(error.message, true); }
    }),
  );
  const exportBtn = el('a', '导出最新结算结果', 'btn');
  exportBtn.href = `/api/settlements/${currentSettlement}/export`;
  exportBtn.addEventListener('click', event => {
    if (!detail.latest_version_no) {
      event.preventDefault();
      notify('尚未生成结算结果，无可导出内容', true);
    }
  });
  wrap.append(exportBtn);
  return wrap;
}
