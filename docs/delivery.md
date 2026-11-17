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
  STEP 头部认不出、STEP 包围盒退化（所有点在同一位置）、有图纸却没写比例、review report 不是 pass、
  `--require` 的类型缺席、**目录里什么都没有**。
- **告警（不影响 exit）**：AP203、DWG 数不了实体、原生零件只验存在性、没有标题栏线索、图纸幅面不是 ISO A、
  线宽只有一种、中文文字没有配 CJK 字体、尺寸文字互相压住、依赖缺失导致某项没查。
- 告警一律带原因，写进 `delivery.json`，不会静默放过。

### 图纸检查逐项

| id | 读什么 | 级别 |
|---|---|---|
| `drawing.dimensions` | modelspace 里 `DIMENSION` 实体数 > 0 | 错误 |
| `drawing.entities` | modelspace 实体数 > 0 | 错误 |
| `drawing.layers` | 图层表非空 | 错误 |
| `drawing.units` | `$INSUNITS` 是 mm/cm/m/inch；`0`（无单位）算错误 | 错误 |
| `drawing.sheets` | 至少一个图纸空间布局里**有实体**（只有 Layout1 不算，默认就有） | 错误 |
| `drawing.sheet` | 出图幅面对 ISO 216（A4–A0，±2mm） | 告警 |
| `drawing.scale` | 图纸上写明「比例 1:2」或 `SCALE 1:2`；`dimlfac` 只作为附注，不算数 | 有图纸时错误 |
| `drawing.titleblock` | 图纸空间有 INSERT 块，或含「比例/材料/图名/SCALE」的文字 | 告警 |
| `drawing.lineweights` | 图层里至少有 2 种显式线宽（粗实线/细实线） | 告警 |
| `drawing.fonts` | 出现中文时，用到的文字样式必须指向仿宋/宋体/黑体/雅黑这类字体 | 告警 |
| `drawing.dim_overlap` | 尺寸文字中点两两距离 < 2mm 的配对数为 0 | 告警 |

### STEP 检查逐项

| id | 读什么 | 级别 |
|---|---|---|
| `step.schema` | `FILE_SCHEMA` 认得出 AP203/214/242（schema 名不是 AP 号：`AUTOMOTIVE_DESIGN`=AP214、`CONFIG_CONTROL_DESIGN`=AP203） | 错误 |
| `step.pmi` | AP203 丢颜色与装配结构 | 告警 |
| `step.entities` | 实体数 > 100 | 错误 |
| `step.bbox` | 用 `CARTESIAN_POINT` 原始坐标算包围盒，全零视为无几何。**近似值**，不是 B-Rep 校验 | 错误 |

## 交付模式

两种，开工前定一次，整批沿用。

| 模式 | 查什么 | 什么时候用 |
|---|---|---|
| `normal`（默认） | 文件本身站不站得住：尺寸实体、单位、图幅、比例、字体、线宽、文字层、STEP schema 与包围盒 | 打样、方案验证、内部结构稿 |
| `half`（半交付） | 上面全部，**再加**：材料、配合、粗糙度必须取自 `lib/standards.json` | 要交给别人看、要能核查取值的图 |

```powershell
dsh-sw-cad mode half        # 设一次，之后 deliver 自动沿用
dsh-sw-cad mode             # 看当前是什么
dsh-sw-cad mode normal      # 切回
dsh-sw-cad deliver D:\work\part-001 --profile half    # 单次覆盖，不改配置
```

优先级：命令行 `--profile` > `~/.dsh/sw-cad.json` 里的 `profile` > `normal`。

**为什么要有两种**：公差和材料是设计决策，不是格式。普通模式下硬卡这些会让打样变得很啰嗦；半交付模式下不卡，交出去的图就只是“看起来专业”。所以做成一次选择，而不是每次打断。

**半交付模式新增的检查**：

| id | 读什么 | 级别 |
|---|---|---|
| `standards.material` | 图纸上出现 `lib/standards.json` 里的某个牌号（45、40Cr、6061…） | 错误 |
| `standards.material_table` | 材料栏写了一个表里没有的值时，单独指出来 | 错误 |
| `standards.fit` | 出现的配合代号（`H7/g6`、`φ40H7` 里的 `H7`）都在表内 | 错误 |
| `standards.roughness` | 出现的 `Ra` 值在优先数系里（0.4/0.8/1.6/3.2/6.3/12.5/25…）；一个 Ra 都没有算错误 | 错误 |

`delivery.json` 里会写 `"profile": "half"` 和 `"not_checked": ["审签…", "尺寸链闭合", "零件可加工性"]`——**半交付的 pass 不是全合规**，这份记录是为了让它不会被误当成全合规。

## 它不查什么

公差配合（`H7/g6`）、材料、热处理、表面处理、尺寸链是否闭合、形位公差数值是否合理、零件能不能加工。
工具允许模型填任意值，仓库里也没有标准表可以对照。**这些仍然需要人看**，`delivery.json` 的 `pass`
不等于可以下厂。

STEP 的包围盒只是"零件有没有被缩放/旋转、导出里有没有几何"的粗筛。真正的几何校验要把 STEP 解析成
B-Rep 再和原生模型比对体积、面数、拓扑，那需要 OCCT（约 200 MB），本仓库没有为此引入依赖。

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
