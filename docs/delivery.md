# 交付检查

`dsh-sw-cad deliver` 是两条 CAD 路线共用的收尾。它不启动任何 CAD，只读已经落盘的文件，所以同一份交付
跑两次结论一致，也可以放进 CI。

## 为什么要它

| 路线 | 收尾现在是什么 | 问题 |
|---|---|---|
| AutoCAD | `drawing_finalize`（8 步验证器，失败抛错）+ `drawing_critique` | 已经够硬，但只覆盖 AutoCAD 自己那套 |
| SolidWorks | 技能要求"导出四视图预览并目视复核" | 是**要求**，模型可以跳过，直接回一句"已完成" |

`deliver` 把判断从"模型说它看了"挪到"文件经不经得起读"：review report 的 `status` 不是 `pass`、
DXF 里没有 `DIMENSION` 实体、PDF 没有文字层、STEP 是 AP203——每一条都会让它返回非零。

## 用法

```powershell
dsh-sw-cad deliver D:\work\part-001                      # 检查目录下所有可识别文件
dsh-sw-cad deliver D:\work\part-001 --require drawing,pdf
dsh-sw-cad deliver D:\work\part-001 --out D:\work\delivery.json --json
```

`--require` 声明这次必须有的交付物类型：`drawing`、`pdf`、`step`、`model`、`report`。

## 判定规则

- **错误（exit 1）**：文件缺失、DXF 无尺寸实体、`$INSUNITS=0`、图纸空间空、PDF 非 ISO A 幅面或没有文字层、
  STEP 头部认不出、review report 不是 pass、`--require` 的类型缺席、**目录里什么都没有**。
- **告警（不影响 exit）**：AP203、DWG 数不了实体、原生零件只验存在性、没有标题栏线索、依赖缺失导致某项没查。
- 告警一律带原因，写进 `delivery.json`，不会静默放过。

## 它不查什么

公差配合（`H7/g6`）、材料、热处理、表面处理、尺寸链是否闭合、形位公差数值是否合理。工具允许模型填任意值，
仓库里也没有标准表可以对照。**这些仍然需要人看**，`delivery.json` 的 `pass` 不等于可以下厂。

## 产物

`delivery.json`：

```json
{
  "verdict": "pass",
  "required": ["drawing", "pdf"],
  "missing": [],
  "counts": { "files": 2, "checked": 2, "failures": 0, "warnings": 0 },
  "files": [
    { "path": "...", "kind": "drawing", "bytes": 74385, "sha256": "…",
      "checks": [{ "id": "drawing.dimensions", "ok": true, "detail": "3 DIMENSION entities in modelspace" }] }
  ]
}
```

每个文件带 SHA-256，是为了让"交付的是这一版"可核对，而不是靠文件名和日期猜。

## 接进流程

- AutoCAD 路线：技能第 6 步已经写了这条命令（`skills/autocad-mcp-pro/SKILL.md`）。
- SolidWorks 路线：技能文本是上游的，本仓库不改它。要让它也走门禁，在任务指令里加一句
  "结束前跑 `dsh-sw-cad deliver <输出目录>`，verdict 不是 pass 就返工"，或者在你的自动化脚本里作为最后一步。

## 依赖

跑在 SolidWorks 那个 venv 上（ezdxf、PyMuPDF 都在里面）。缺依赖时相关检查降级为告警并写明原因，
不会崩，也不会假装通过。
