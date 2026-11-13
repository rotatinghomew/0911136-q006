# 领域资料与供数约定

## 数据含义

cells.value 可为 null，表示未知。unit 为 person 或 household；约束内只能比较同量纲数值。terms 是 cell_id 到有理数系数的映射，约束为线性等式，误差容许度为 tolerance 与各 cell 声明误差带共同决定。scope_members 为该表覆盖的村集合，重叠表不能直接累加。修订 form 明确 supersedes，原表留档；source_ref 指向本代源单稳定编号。现有摘要仅计算各约束的数值残差，不自动选择修正集合。

## 现行摘要

`warning_metrics/analysis.py` 生成现行资料摘要。源码和运行结果中的 scope 说明其统计边界，不能把未提供的推断当成已有能力。

## 供数接口

`GET /manifest` 返回 generation、tables（每表 count 与 sha256）及 metadata。`GET /tables/<name>?generation=<generation>` 返回同代 rows。供数换代时旧 generation 返回 HTTP 409，不允许将新代记录接入旧分析；不存在的表返回 404。表内容为稳定键唯一的完整同代记录，不是增量事件流。客户端对清单摘要进行核验。摘要使用 UTF-8、排序对象键、紧凑 JSON 的 SHA256，数组顺序保留供数端顺序。

字段与主键声明位于 `warning_metrics/schema.json`，合成资料位于 `fixtures/dataset.json`。`metadata.cutoff` 是供数展示截点，不自动为分析选择训练截止点。服务可将整个 dataset 原子替换为新一代，但不在同一 generation 下更改内容。

## 结果保存

`reports.py` 保存现有摘要及来源表摘要，report_id 是内容摘要，不是随机编号。相同内容生成相同编号，临时文件写入完成后原子替换目标文件；历史内容是否保留由调用者选择不同目标路径。服务只监听本机，不接入生产系统。
