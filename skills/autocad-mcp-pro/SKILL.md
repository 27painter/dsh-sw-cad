---
name: autocad-mcp-pro
description: "AutoCAD 制图自动化技能（2026/2027 已验证，2023–2025 预期可用）。当任务涉及绘制或修改 DWG/DXF 图纸、机械制图（尺寸标注/图框/标题栏/明细表/紧固件/齿轮/键槽）、建筑平面图（墙/门/窗/面积）、P&ID 工艺图、读取分析现有图纸（算量/提取明细表/版本比对）、或导出 PDF/PNG 时使用。通过 autocad MCP 工具按需检索执行，所有交付必须复核。触发词：AutoCAD、DWG、DXF、CAD、画图、出图、图层、标注、图框、标题栏、明细表、机械制图、批量改图。"
metadata: { "os": ["win32"], "requires": { "anyBins": ["python", "py"], "pythonPackages": ["pywin32", "ezdxf"] } }
---

# AutoCAD 自动化技能（autocad-mcp-pro）

通过 MCP 工具操控本机 AutoCAD（2026/2027 已验证，2023–2025 预期可用）。工具不预加载：服务器处于 **discovery 模式**，会话中只有 `search_tools` 和 `call_tool` 两个元工具（空闲约 356 token），完整 242 工具目录按需检索。

## 环境预检（每次任务开始）

1. **AutoCAD 是否已运行**：COM 实控需要。没开就提示用户启动，或降级 ezdxf 无头引擎（仅 DXF，无渲染）并在结果中注明。
2. **权限一致**：dsh/Codex 必须与 AutoCAD 同用户、同完整性级别启动。
3. **工作目录**：输出放在 `D:\AutoCad`（ALLOWED_PATHS 白名单）内。
4. 可用 `system_about` 确认运行时后端与版本；`system_capabilities` 查当前工具集。

## 工具调用方式（按需检索）

不要凭记忆猜工具名。先 `search_tools` 用关键词检索，再 `call_tool` 调用。

- 语料库覆盖 **206 个 AutoCAD 命令名 + 1439 个同义词**：`FILLET`（倒角/圆角）、`EXTRUDE`（拉伸）、`TRIM`（修剪）、`BPOLY`（边界）、`WBLOCK`（写块）、`QSELECT`（快速选择）、`OVERKILL`（删除重复）、`CHSPACE`（空间转换）等均排第 1。
- 坐标系统一 **WCS**（唯一例外：TEXT 的 rotation 在实体坐标系，镜像文字无法用标量角度表达）。
- 每个工具有 `_meta.cad` 锚点（cost/acad 命令/synonyms），检索时优先用 AutoCAD 命令名。
- 工具目录为**全量 242 个**（服务器 env: `TOOL_PROFILE=full` + `TOOL_PACKS=all`）：机械、建筑、工艺、算量各包工具全部可检索；discovery 模式下空闲仍只广告 2 个元工具，目录大小不占常驻 token。

## 核心工具速查（按工作流，检索锚点）

| 阶段 | 工具 |
|---|---|
| 文档 | `drawing_new`（自动建工程图层+线型）、`drawing_open/save/save_as`、`document_list/activate`、`drawing_audit/purge` |
| 几何 | `entity_create_line/circle/arc/polyline/rectangle/text/mtext/table/hatch/spline/block_ref`、`entity_edit_geometry`、`selection_window/polygon/filter` |
| 编辑 | `entity_move/copy/rotate/scale/mirror/offset/trim/extend/fillet/chamfer`、`entity_array_rectangular/polar`、`entity_batch_create/modify`、`cad_batch` |
| 标注 | `dimension_linear/aligned/angular/radius/diameter`、`dimension_auto`、`dimstyle_*`、`textstyle_*`、`mleaderstyle_*`、`gd_frame`（几何公差框）、`datum_feature`、`surface_texture`、`weld_symbol`、`centre_marks` |
| 机械 | `mech_part_draw/from_spec/inspect`、`mech_view_add`、`mech_dimension_part`、`mech_hole_pattern`、`gear_draw_spur_front_view`、`gear_draw_helical_front_view`、`gear_draw_section_aa`、`keyway_draw_keyed_bore`、`keyway_draw_section`、`std_part_list/insert`（ISO 紧固件/轴承）、`std_feature_draw` |
| 图纸完备 | `sheet_frame`、`titleblock_apply_iso_a3`、`titleblock_apply`、`revision_add`、`bom_extract`、`bom_table`、`balloon_add`、`xref_attach/manage`、`image_attach` |
| 建筑 | `arch_wall/opening/stair/room/rooms_detect/schedule`、`arch_plan_from_spec`（一句规划全图）、`arch_dimension_chains`、`arch_catalogue_*`、`arch_grid/symbol` |
| P&ID | `pid_symbol_insert/list`、`pid_line_draw`、`pid_from_spec`、`pid_graph`、`pid_instrument_index/line_list/equipment_list` |
| 布局出图 | `layout_*`、`viewport_*`、`page_setup_apply`、`batch_plot`、`drawing_export_pdf/dxf/dwg` |
| 分析理解 | `drawing_understand`（读懂他人图纸）、`drawing_scale_check`、`drawing_diff`、`drawing_topology_check`、`analysis_measure_*`、`pipe/cable_takeoff`、`data_extract` |
| 事务与系统 | `transaction_begin/commit/rollback`（带 undo）、`system_run_command`、`system_run_lisp`、`system_launch` |

## 服务器内置工作流纪律（来自 server instructions，必须遵守）

1. **工程图**：`drawing_new` 会自动引导出工程图层和线型，不要手工从零建层。
2. **图框/标题栏**：用 `titleblock_apply_iso_a3`（ISO 7200），**绝不手画**；标题文字原样传入，不要发明变体。
3. **齿轮/键槽/剖面线**：用参数化工具 `gear_draw_*` / `keyway_draw_*`，**禁止**用基本图元手画齿形。
4. **标注**：用 `dimension_*` 生成真实 DIMENSION 实体，**禁止**用 text+leader 冒充尺寸。
5. **收尾**：`drawing_finalize` 是 8 步验证器 + 保存 + 截图，**不过它不能宣称完成**（验证失败会抛错）；随后 `drawing_critique` 应返回 0 issues，并重开 DXF 复核几何、核对 PDF `/MediaBox` 幅面。**工具返回成功 ≠ 任务完成。**

## 已验证能力要点

- 机械：渐开线齿轮（模数/齿数/压力角驱动）、DIN 6885 键槽、ISO 4014/4017/4032/7089/4762 紧固件、ISO 15 轴承（真实块+属性）。
- 理解/算量：`drawing_understand` 一键读懂他人图纸；`drawing_diff` 版本比对；`pipe_takeoff`/`cable_takeoff` 管/电缆算量（输出多语言工作簿）。
- 无头：ezdxf 后端可多文档并行、图层状态、图案填充、边界追踪；**无渲染、读不了 ACIS（3DSOLID/REGION 面积）**。

## 已知坑（来自项目真机回归，务必遵守）

1. ActiveX 拒绝在缺失图层上设 `entity.Layer`（ezdxf 会静默创建）→ **先建图层**。
2. ActiveX 中块引用叫 `BLOCKREFERENCE` 不是 `INSERT`。
3. pywin32 类型库缓存会导致所有实体返回裸 `IAcadEntity`（无线端点）→ 重启 MCP 服务器。
4. WIPEOUT、MTEXT 背景色在 AutoCAD 2026 实测缺失 → 此类需求走无头引擎。
5. 无头 CHSPACE 仅限顶视未扭曲视口；尺寸/ACIS/代理实体/表格会被拒绝。
6. 版本说明：COM 路径在 **AutoCAD 2026 真机验证**，2027 的 ActiveX API 兼容；2025/2024/2023 走同一套 ActiveX 接口，预期可用但未验证——首次使用先跑服务器仓库的 `scripts\smoke_*_com.py` 确认。ezdxf 无头后端与 CAD 版本无关（不需要 AutoCAD）。

## 边界

- 不用于：纯效果图渲染、BIM/Revit、STEP-first 三维建模、CAM 刀路。
- 修改既有 DWG 前先另存副本。
- 无人值守交付前，机械图纸至少人工目视一遍 `view_screenshot` 预览图。
