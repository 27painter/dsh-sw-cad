# dsh-sw-cad

[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**两个 MCP 服务器 + 两个 Skill，让 DeepSeek Harness 或 Codex 的 agent 真正操控 CAD 软件。**
它们封装了两个现成的开源项目——[U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP) 和
[wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill)——
让 agent 能在 AutoCAD 里画图、在 SolidWorks 里建模，并把结果导出。本仓库是**配置层**：隔离环境、启动参数、
按需加载、客户端注册。它不 fork、不魔改、不重新分发这两个服务器。

[English](README.md) | 中文

## 它做什么，什么时候需要它

一个能"看懂"图纸的 CLI agent 有用，一个能"产出"图纸的 agent 是另一回事。装上本配置包后，dsh 或 Codex
能接住这样一句话——"按 ISO A3 图框画一个 M6 螺纹孔支座，标注后导出 PDF"——并且真的做出来：建图层、画几何、
标尺寸、盖标题栏、跑验证器，最后把文件交给你。

代价和所有 COM 自动化一样：**CAD 软件必须开着，agent 必须和 CAD 同 Windows 用户、同完整性级别运行。**
这不是云服务，原生 `.dwg` / `.sldprt` 输出也没有无头替代方案。

两个 Skill 是入口。它们按需加载——只有当请求命中 SKILL.md 的 description 时才被读取，所以空闲会话不为此
付出任何上下文成本：

| Skill | 工作方式 |
|---|---|
| `solidworks-automation` | 脚本驱动：66 个 Python 模块（30 个 `sw_*.py` COM 封装 + OCCT/FEA/DFM 工具），自带预检、能力门禁与证据链 |
| `autocad-mcp-pro` | 本仓库原创（上游没有 AutoCAD skill），通过两个元工具驱动 AutoCAD MCP |

两个 MCP 服务器是肌肉，注册在客户端配置里：

| 服务器 | 工具数 | 模式 |
|---|---|---|
| `autocad` | 242 | `DISCOVERY_MODE=search`：只广告 `search_tools` + `call_tool`（空闲约 356 token），完整目录按关键词检索 |
| `solidworks` | 66 | 全量广告（50 个 `solidworks_*` + 14 个 `cadstudio_*` + 2 个 `design_*`） |

```
你说人话
   │
   ├─ dsh / Codex 按 SKILL.md 的 description 按需加载 Skill
   │      ├─ solidworks-automation  → sw_*.py 脚本 → COM → SolidWorks
   │      └─ autocad-mcp-pro        → search_tools / call_tool → MCP → AutoCAD
   │
   └─ MCP 服务器（cordis.patch.yml / codex config.toml）
          ├─ autocad    discovery 模式，242 工具按需检索
          └─ solidworks 66 工具 stdio 服务器
```

## 哪些是上游的，哪些是本仓库的

这一点在这里比通常更重要——两个服务器都是别人的成果。分工如下：

| 部分 | 来源 | 许可证 |
|---|---|---|
| AutoCAD MCP 服务器（`autocad-mcp-pro`） | [U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP)，从 PyPI 安装 | MIT |
| SolidWorks Skill + MCP 服务器 | [wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill)，安装时克隆 | MIT |
| `skills/autocad-mcp-pro/SKILL.md` | 本仓库原创——上游没有 AutoCAD skill | MIT |
| `install.ps1`、`cordis.patch.yml`、`codex-config.snippet.toml`、两份 README | 本仓库原创 | MIT |

**上游代码一行都没有进仓库。** 安装脚本在运行时才去 PyPI 取包、克隆（或 zip 下载）SolidWorks 仓库，所以
你拿到的永远是上游当前版本，著作权也始终归上游。本仓库补的是它们缺失的那一层：经过验证的启动参数、
discovery 模式配置、按需 Skill 接线、把几个 G 的依赖挡在 C 盘之外的 junction 技巧，以及一个在 git 协议
被拦时还能装完的安装器。

## 安装

**路线 1 —— 一个脚本。** 在 `D:\cad-agents` 下建两个 venv（用 `-BaseDir` 改位置）、装两个服务器、克隆
SolidWorks skill、junction 进 `~/.dsh/skills`，并写入 `~/.dsh/cordis.patch.yml`（原文件自动带时间戳备份）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**路线 2 —— 手动。** 把 `cordis.patch.yml` 拷进 `~/.dsh/cordis.patch.yml`（按需改路径），再跑一次安装脚本
建环境；或者自己建 venv，把 patch 里的路径指过去。

**Codex 用户多一步**——把 `codex-config.snippet.toml` 追加到 `~/.codex/config.toml` 末尾，再把技能 junction 进去：

```powershell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.codex\skills\solidworks-automation" -Target "D:\cad-agents\dsh-skills\solidworks-automation"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.codex\skills\autocad-mcp-pro"       -Target "D:\cad-agents\dsh-skills\autocad-mcp-pro"
```

> [!IMPORTANT]
> **先开 CAD，并且同用户启动。** COM 自动化要跨一个被 Windows 按用户和完整性级别守护的进程边界。
> 如果 agent 以管理员身份启动、CAD 以普通身份运行（或反过来），它什么也连不上，而失败现象看起来只是
> "工具什么都没干"。

## 使用

直接描述要画的东西，Skill 会自己加载。

```text
用 AutoCAD 画一个轴承座：底座 120×80×10，立板高 60 厚 12，顶部 φ40 通孔。
ISO A3 图框带标题栏和尺寸标注，存到 D:\AutoCad 并导出 PDF。
```

```text
在 SolidWorks 里建一个法兰：外径 100，厚 20，中心孔 30，均布 4 个 M10 螺栓孔。
存 SLDPRT 并导出 STEP。
```

几条踩过坑的经验：

- **一次只说一个 CAD。** 同时提两个会加载两个 Skill，任务也容易混。
- **复杂任务先要计划。** 两个 Skill 都有规划环节；在画几何之前先审计划，比画错了再撤销便宜。
- **主动要预览。** AutoCAD 路线最后有 8 步验证器加截图；SolidWorks 路线会导出四张正交预览图。
  说一句"把预览图给我看"是合理要求，Skill 会照做。

## 验证

```powershell
# dsh 是否看见两个服务器、两个技能
pnpm dsh --profile headless --dump-config
pnpm dsh --profile headless "列出当前可用的 skill"

# SolidWorks 技能自检（Python 依赖 + COM）
& "D:\cad-agents\venv-solidworks\Scripts\python.exe" "D:\cad-agents\dsh-skills\solidworks-automation\scripts\sw_preflight.py"

# AutoCAD MCP 能起来并报告版本
& "D:\cad-agents\venv-autocad\Scripts\autocad-mcp.exe" --help

# Codex
codex mcp list
```

发布前实际验证过的：两个 MCP 服务器都能完成 JSON-RPC 握手（`autocad-mcp-pro` v3.4.11、`solidworks_mcp`
v1.30.0）；discovery 模式只广告两个元工具，且能按关键词检索到 `gear_draw_spur_front_view` /
`keyway_draw_keyed_bore`；`dsh --dump-config` 两条注册都能加载；SolidWorks 自检通过。

## 设计说明

**1. discovery 模式是这套配置的核心。** 242 个工具定义全量广告约 8.4 万 token。discovery 模式下客户端只看到
两个工具（约 356 token），检索目录里带 206 个 AutoCAD 命令名 + 1439 个同义词——说"倒角"能找到 `FILLET`。

**2. `TOOL_PROFILE` 保持 `full`、`TOOL_PACKS` 保持 `all`。** 它们只收缩"可检索目录"，不改变空闲成本。
实测 `lean + core,mech` 会把目录砍到 58 个工具，参数化齿轮和键槽工具直接搜不到——收缩 packs 只丢能力，
一个 token 也省不下。

**3. 用 junction，不复制。** dsh 只扫 `~/.dsh/skills`，Codex 只扫 `~/.codex/skills`。目录联接把两边都指到
数据盘，两个工具都无感知。删联接不删数据。

**4. 把文件访问圈起来。** AutoCAD 服务器的 `ALLOWED_PATHS` 默认是空的，它自己的 README 也要求你设置。
本配置包把它设为一个工作目录。

**5. 分清什么验证过。** AutoCAD COM 在 2026 上真机验证；2027 的 ActiveX API 兼容，但建议先跑上游的 smoke
脚本。SolidWorks 由上游在 2026 SP4.1 上验证。

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| dsh 列不出 skill | `~/.dsh/skills` 必须是 junction 且目标存在；目录名必须等于 frontmatter 的 `name` |
| Codex 列不出 skill | `~/.codex/skills\` 下两个 junction 是否都在；Codex 版本需支持 skills |
| AutoCAD 工具调了没反应 | CAD 没开，或用户/完整性级别不一致；设 `AUTOCAD_MCP_BACKEND=com` 排除 ezdxf 降级 |
| SolidWorks 自检不过 | 缺依赖（装 skill 的 `requirements.txt`）或 SW 没开 |
| `codex mcp list` 为空 | 追加段必须在 `config.toml` 末尾；路径要双反斜杠 |
| 安装时 `git clone` 失败 | 安装器会自动降级到 codeload zip 下载 |

## 许可证

MIT（见 [LICENSE](LICENSE)）。上游组件保持各自 MIT 许可证，本仓库不重新分发它们。
