# 机坪周转核算深度登记

- 规模：中型
- 核心规则：按分钟计算保障占用，合并多个豁免区间的交集，再按 15 分钟进位计费。
- 使用者与工作流：机场运行结算员按保障单复算来源批次，对照外部记录后提交复核。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 来源采信 | 接收分段记录、定位坏行 | `POST /api/intake` | `app/modules/intake/service.py` | `tests/test_intake.py` |
| M2 字段解释 | 归一主键、版本、时间和原字段 | `POST /api/contract` | `app/modules/contract/service.py` | `tests/test_contract.py` |
| M3 隔离裁决 | 归并重复、隔离争议版本 | `POST /api/quality` | `app/modules/quality/versions.py` | `tests/test_quality.py` |
| M4 区间计费 | 复算业务明细与边界证据 | `POST /api/domain` | `app/modules/domain/rule.py` | `tests/test_domain.py` |
| M5 独立对账 | 核对外部记录并分别计量 | `POST /api/reconcile` | `app/modules/reconcile/service.py` | `tests/test_reconcile.py` |
| M6 批次复算 | 保存运行、查询历史、复算原输入 | `POST /api/runs` | `app/modules/runs/service.py` | `tests/test_runs.py` |
| M7 交付复核 | 复核差异、阻止陈旧提交、导出证据 | `GET /api/export` | `app/modules/review/decisions.py` | `tests/test_review.py` |
| M8 关账调整 | 已确认账单只读、晚到修订预览影响、逐单裁决并导出新结算 | `POST /api/settlements` | `app/modules/settlement/service.py` | `tests/test_settlement.py` |

## 陷阱登记

| TRAP | 现象 | 样例 | 处理位置 | 测试 |
|---|---|---|---|---|
| TRAP-02 | BOM | shift.csv | `app/modules/intake/service.py` | `tests/test_intake.py::test_bom` |
| TRAP-03 | 引号内换行 | shift.csv | `app/modules/intake/service.py` | `tests/test_intake.py::test_multiline` |
| TRAP-04 | 重复表头 | shift.csv | `app/modules/intake/service.py` | `tests/test_intake.py::test_repeated` |
| TRAP-05 | 列数不齐 | shift.csv | `app/modules/intake/service.py` | `tests/test_intake.py::test_width` |
| TRAP-07 | 空值哨兵 | shift.csv | `app/modules/contract/service.py` | `tests/test_contract.py::test_missing` |
| TRAP-08 | 主键前导零 | shift.csv | `app/modules/contract/service.py` | `tests/test_contract.py::test_leading_zero` |
| TRAP-11 | 偏移时间归一 | shift.csv | `app/modules/contract/service.py` | `tests/test_contract.py::test_offsets` |
| TRAP-14 | 等版本冲突 | shift.csv | `app/modules/quality/versions.py` | `tests/test_quality.py::test_conflicting` |
| TRAP-18 | 三种空态 | 测试构造输入 | `app/modules/intake/service.py` | `tests/test_intake.py::test_empty_states` |
| TRAP-26 | 陈旧复核 | 测试构造输入 | `app/modules/review/decisions.py` | `tests/test_review.py::test_stale` |
| TRAP-23 | 相邻豁免端点只属于一个区间 | cases.csv | `app/modules/domain/rule.py` | `tests/test_domain.py::test_boundary` |

## 对账规则

输入记录数 = 计入 + 隔离 + 去重和替换 + 空白记录 + 重复表头。
业务账按保障单全外连接。差异分别标记漏记、外部单边和数值不符，以计费分钟对差。

## 关账调整（M8）

- 关账单只能挂在最新复核结论为「确认」的批次上；原批次结果只读，任何修订都不回写。
- 晚到修订按保障单对照原确认批次：仅候选修订号高于原修订才计入，预览原/新计费分钟、分钟差、外部账差值前后变化；外部账缺键时外部差留空但不阻断。
- 复核员对每条调整裁决「纳入本次」或「留待下期」，均须填意见；未确认（pending）调整不进入任何结算结果。
- 结算版本以原确认账单为底，仅替换纳入调整对应的保障单，再按原外部账重新独立对账；留待下期与未确认项保持原值并列明。
- 同一保障单出现更高修订时，旧候选标记 `superseded` 留痕；同一修订重跑按 `(关账单,保障单,候选修订)` 幂等，载荷冲突返回 409 且保留旧记录。
- 封账要求不存在未确认调整且至少有一个结算版本；封账后登记、裁决、再出版本一律 409，正式结果固定并可经 `/api/settlements/{id}/export` 导出。
- 候选批次复算失败（坏数据等）不写入任何调整；结算/封账失败不改变既有版本。

## 范围

本地文件与单机复核。单次来源文本限 128 KiB。复合字段单项最多 240 段。
