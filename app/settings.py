import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / 'sample_data'
DB = Path(os.getenv('WORKBENCH_DB', str(ROOT/'state/ledger.sqlite3')))
TITLE = '机坪周转核算'
SLUG = 'apron-turnaround'
KEY = '保障单'
FIELDS = ('起分钟', '止分钟', '豁免窗口', '每单位时长')
ROLE = '机场运行结算员'
UNIT = '计费分钟'
SIGNATURE = '值机时间轨'
RULE_VERSION = 'apron-turnaround-1.0'
TABS = {'intake': '来源采信', 'contract': '字段解释', 'quality': '隔离裁决', 'domain': '区间计费', 'reconcile': '独立对账', 'runs': '批次复算', 'review': '交付复核'}
