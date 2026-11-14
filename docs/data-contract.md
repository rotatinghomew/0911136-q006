# 领域资料与供数约定

## 数据含义

cells.value 可为 null，表示未知。unit 为 person 或 household；约束内只能比较同量纲数值。terms 是 cell_id 到有理数系数的映射，约束为线性等式，误差容许度为 tolerance 与各 cell 声明误差带共同决定。scope_members 为该表覆盖的村集合，重叠表不能直接累加。修订 form 明确 supersedes，原表留档；source_ref 指向本代源单稳定编号。现有摘要仅计算各约束的数值残差，不自动选择修正集合。

## 现行摘要

`warning_metrics/analysis.py` 生成现行资料摘要。源码和运行结果中的 scope 说明其统计边界，不能把未提供的推断当成已有能力。

## 统计口径一致性诊断

`warning_metrics/diagnosis.py` 在不改动源表的前提下做简报发出前的一致性诊断，运行入口为 `python3 -m warning_metrics.runner --diagnosis`，默认摘要调用保持不变。诊断只读 `tables`，不回写任何单元格、不生成"看起来平衡"的替代表，输出只含证据、候选记录集合与人工修正建议，放行结论由复核人员作出。

诊断使用的口径与边界：

- **跨表约束与误差带**：逐条复算线性等式残差，容许度为约束 `tolerance` 加各 cell 声明误差带按系数绝对值加权；落在带内判 balanced，超出判 conflict，含 null 的判 unknown。
- **候选解释集合**：把"修正量 d"作为未知量建立 `A·d = -r` 方程组（r 为当前残差），用精确有理数（Fraction）做 Gauss-Jordan 可行性检验，枚举能同时解释全部冲突约束的最小单元格集合。候选变量不触及某条冲突约束时该集合直接不可行；存在多个同等合理集合时全部返回、保留歧义，不选择唯一修正。满秩且唯一整数解时才给"其他单元格不变"条件下的修正量，欠定集合只标注保留歧义；修正量超出该 cell 声明误差带会单独提示，需源单位重新上报而非汇总环节代改。
- **量纲隔离**：person 与 household 的冲突分开枚举，候选集合不跨量纲；约束内混合量纲直接报错。模块不做人口数/户数相除，也不据比值推断哪个村瞒报。
- **缺报不是零**：`value=null` 始终按未知处理，不参与平衡、不以 0 补齐；另核对约束引用与表单引用完整性，悬空引用进入 advisories。
- **上下级与重叠范围**：按 `forms.scope_members` 识别从属、局部重叠关系，提示重叠范围不可直接累加；同 scope、同指标、同量纲被多张有效表单上报时归为重复上报嫌疑组，并附 `source_ref` 供追溯。
- **修订链追溯**：校验 `supersedes` 指向存在且无环，区分全量修订与局部修订（按 scope 交集）；被取代表单仍被约束引用时提示按源单确认；无 `source_ref` 或引用未知表单的单元格作为证据链不完整处理。

枚举设单元格数与集合数上限（`MAX_CANDIDATE_CELLS`、`MAX_CANDIDATE_SETS`），达到上限会在结果中显式标注 truncated 并给出对应 advisory，不假装已经穷举。

## 供数接口

`GET /manifest` 返回 generation、tables（每表 count 与 sha256）及 metadata。`GET /tables/<name>?generation=<generation>` 返回同代 rows。供数换代时旧 generation 返回 HTTP 409，不允许将新代记录接入旧分析；不存在的表返回 404。表内容为稳定键唯一的完整同代记录，不是增量事件流。客户端对清单摘要进行核验。摘要使用 UTF-8、排序对象键、紧凑 JSON 的 SHA256，数组顺序保留供数端顺序。

字段与主键声明位于 `warning_metrics/schema.json`，合成资料位于 `fixtures/dataset.json`。`metadata.cutoff` 是供数展示截点，不自动为分析选择训练截止点。服务可将整个 dataset 原子替换为新一代，但不在同一 generation 下更改内容。

## 结果保存

`reports.py` 保存现有摘要及来源表摘要，report_id 是内容摘要，不是随机编号。相同内容生成相同编号，临时文件写入完成后原子替换目标文件；历史内容是否保留由调用者选择不同目标路径。服务只监听本机，不接入生产系统。
