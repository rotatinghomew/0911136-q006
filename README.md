# 联动日报数值矛盾的跨表约束诊断

本项目组织领域资料并提供当前摘要，通过本地 HTTP 供数服务连接应用。业务代码在 `warning_metrics`，字段与单位约定见 `docs/data-contract.md`。示例均为本地合成记录，不代表真实县域记录或业务结论。

## 环境

Python 3.11 及以上，业务运行仅使用标准库。构建工具及其依赖完整固定在 `requirements-build.txt`；先用 `python3 -m pip install -r requirements-build.txt` 安装。源码和业务测试无需访问外部服务，首次安装构建依赖需要访问 Python 包仓库。

## 当前摘要

在项目根目录运行，会临时启动真实本地 HTTP 服务、核验资料代际、计算现有摘要并完整打印 JSON：

```sh
python3 -m warning_metrics.runner --output /tmp/warning_metrics-summary.json
```

当前能力是源码 `analysis.py` 定义的摘要，不会自动给出其他统计推断。输出中的 scope 说明边界，sources 记录资料依据。

## 统计口径一致性诊断

简报发出前的人工核对可加 `--diagnosis`，在现行摘要之外追加一致性诊断（只读源表，不修改上报数值、不生成平衡替代表）：

```sh
python3 -m warning_metrics.runner --diagnosis --output /tmp/warning_metrics-diagnosis.json
```

诊断把跨表线性约束与误差带、表单 `scope_members` 的上下级/重叠范围、`supersedes` 修订链与 `source_ref` 组合起来，输出能解释矛盾的最小候选记录集合、证据与修正建议。多个同等合理解释全部保留、保留歧义；缺报（null）按未知不按零；person 与 household 分开诊断、不做人口/户数相除式认定；重复上报与局部修订可追溯到源单。放行由复核人员看证据后决定，字段与口径边界见 `docs/data-contract.md`。

## 分开运行供数与应用

```sh
python3 -m warning_metrics.runner --serve --port 8765
```

另一个终端可运行：

```sh
python3 -m warning_metrics.runner --source-url http://127.0.0.1:8765 --output /tmp/warning_metrics-summary.json
```

## 测试与检查

```sh
python3 -m unittest discover -s tests -t . -v
python3 -m compileall -q warning_metrics tests
python3 -m build --wheel --no-isolation
```

测试覆盖现有领域摘要、非法引用、真实 HTTP 换代拒绝、表清单内容核验及结果保存。展示现有产品可直接运行上述摘要命令，不需要回放历史日志。扩展分析时应同步说明新增能力的实际运行入口，保留现行摘要调用。

## 容器运行

Dockerfile 固定官方 Python 3.11.14 slim-bookworm 镜像摘要，不依赖本机私有镜像；首次构建需下载基础镜像和固定构建工具。构建与运行实际摘要：

```sh
docker build -t local/warning-metrics .
docker run --rm local/warning-metrics
```

容器内测试：

```sh
docker run --rm --entrypoint python3 local/warning-metrics -m unittest discover -s tests -t . -v
```
