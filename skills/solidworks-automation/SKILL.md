---
name: solidworks-automation
description: "SolidWorks 建模与出图自动化。当任务涉及 SolidWorks 零件、装配、草图、拉伸/旋转/切除、工程图、导出 SLDPRT/SLDASM/STEP，或通过 solidworks MCP 工具执行建模与检查时使用。触发词：SolidWorks、SW、零件、装配、草图、拉伸、切除、工程图、SLDPRT、SLDASM、STEP。"
metadata: { "os": ["win32"], "requires": { "anyBins": ["python", "py"], "pythonPackages": ["pywin32"] } }
---

# SolidWorks 自动化技能

本技能是 Codex 插件里的入口。完整的上游技能、66 个 Python 模块和 `mcp-server/server.py` 由
`dsh-sw-cad serve solidworks` 在首次使用时安装到本机，不随插件重复分发。

## 定位上游技能

1. 若 `dsh-sw-cad` 在 PATH 中，运行 `dsh-sw-cad where`。
2. 否则找到本 `SKILL.md` 的绝对路径，插件根目录是它向上两级，再运行：

```powershell
node "<插件根目录>\bin\dsh-sw-cad.mjs" where
```

输出里的 `solidworks server` 是 `<baseDir>\dsh-skills\solidworks-automation\mcp-server\server.py`。
完整技能根目录是它向上两级。先读其中的 `SKILL.md`，再按任务需要读对应模块和参考文件。

如果路径不存在，先运行 `install`：

```powershell
node "<插件根目录>\bin\dsh-sw-cad.mjs" install
```

## MCP 工具

SolidWorks MCP 使用 discovery 模式，只常驻三个入口工具：

1. `sw_search_tools`：按关键词搜索目录。
2. `sw_describe_tool`：查看选中工具的完整参数说明。
3. `sw_call_tool`：调用工具。

不要凭记忆猜工具名。SolidWorks 的调用载荷要放在 `params` 下，例如：

```json
{
  "name": "solidworks_sketch_and_extrude",
  "arguments": {
    "params": {
      "shape": "rectangle",
      "width": 100,
      "height": 60,
      "depth": 10
    }
  }
}
```

## 工作纪律

1. SolidWorks 必须由同一 Windows 用户启动，agent 与应用的完整性级别也要一致。
2. 复杂任务先给建模步骤和关键尺寸，确认后再批量调用。
3. 每次保存后核对活动文档、单位、实体数和关键尺寸。
4. 交付前导出 STEP 和四视图/审阅报告，再运行交付门禁：

```powershell
node "<插件根目录>\bin\dsh-sw-cad.mjs" deliver <输出目录> --require step
```

需要材料、配合和粗糙度取自标准表时，使用 `mode half` 或 `deliver --profile half`。
