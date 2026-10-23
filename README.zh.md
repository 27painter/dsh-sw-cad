# dsh-sw-cad

[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**两个 MCP 服务器 + 两个 Skill，让 DeepSeek Harness 或 Codex 的 agent 真正操控 CAD 软件。**
它们封装了两个现成的开源项目——[U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP) 和
[wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill)——
让 agent 能在 AutoCAD 里画图、在 SolidWorks 里建模，并把结果导出。本仓库是**配置层**：隔离环境、启动参数、
按需加载、客户端注册。它不 fork、不魔改、不重新分发这两个服务器。上游缺什么能力，本仓库就在它前面补一层
——比如 SolidWorks 没有办法收起自己那 66 个工具，那就加一个垫片，而不是动它。

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

| 服务器 | 广告工具数 | 空闲成本 | 模式 |
|---|---:|---:|---|
| `autocad` | 2 | 约 356 token | 上游自带 `DISCOVERY_MODE=search`：只广告 `search_tools` + `call_tool`，242 个工具的目录按关键词检索 |
| `solidworks` | 2 | 256 token | 本仓库的 `mcp-shim/` 挡在上游 66 工具服务器前面（50 个 `solidworks_*` + 14 个 `cadstudio_*` + 2 个 `design_*`），后者全量广告要 23,404 token |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/token-cost-dark.svg">
  <img alt="常驻上下文成本柱状图：AutoCAD 直连 83,717 token，SolidWorks 直连 23,404，两个都直连 107,121，本仓库配置 612" src="docs/token-cost-light.svg">
</picture>

*工具定义是在你提出第一个请求之前就要付掉的。两台服务器直连要 107,121 token，走本仓库这套配置是 612，
省 99.4%。数字可用 `mcp-shim/verify_shim.py` 复现。*

```
你说人话
   │
   ├─ dsh / Codex 按 SKILL.md 的 description 按需加载 Skill
   │      ├─ solidworks-automation  → sw_*.py 脚本 → COM → SolidWorks
   │      └─ autocad-mcp-pro        → search_tools / call_tool → MCP → AutoCAD
   │
   └─ MCP 服务器（cordis.patch.yml / codex config.toml）
          ├─ autocad    上游 discovery 模式，242 工具按需检索
          └─ solidworks 本仓库垫片，2 个元工具，66 工具按需检索
                          └─ 首次调用时才拉起上游 server.py
```

## 哪些是上游的，哪些是本仓库的

这一点在这里比通常更重要——两个服务器都是别人的成果。分工如下：

| 部分 | 来源 | 许可证 |
|---|---|---|
| AutoCAD MCP 服务器（`autocad-mcp-pro`） | [U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP)，从 PyPI 安装 | MIT |
| SolidWorks Skill + MCP 服务器 | [wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill)，安装时克隆 | MIT |
| `skills/autocad-mcp-pro/SKILL.md` | 本仓库原创——上游没有 AutoCAD skill | MIT |
| `mcp-shim/`（SolidWorks discovery 垫片 + 词表） | 本仓库原创——上游没有等价机制 | MIT |
| `install.ps1`、`cordis.patch.yml`、`codex-config.snippet.toml`、两份 README | 本仓库原创 | MIT |

**上游代码一行都没有进仓库。** 安装脚本在运行时才去 PyPI 取包、克隆（或 zip 下载）SolidWorks 仓库，所以
你拿到的永远是上游当前版本，著作权也始终归上游。本仓库补的是它们缺失的那一层：经过验证的启动参数、
discovery 模式配置、给缺这个机制的那台服务器补的垫片、按需 Skill 接线、把几个 G 的依赖挡在 C 盘之外的
junction 技巧，以及一个在 git 协议被拦时还能装完的安装器。

## 安装

> [!TIP]
> **不想自己敲这些命令？** 把下面这段发给你的 agent（dsh、Codex，或任何能在这台机器上跑命令的 agent），
> 它会替你装完。这段话把该克隆什么、装到哪、什么不能动都写清楚了——后面几节只在出问题时才需要看。

```text
在这台 Windows 机器上安装 https://github.com/27painter/dsh-sw-cad。

1. 克隆到 D:\dsh-sw-cad。git 被代理拦了就用 codeload zip 下载。
2. 用 -BaseDir D:\cad-agents -WorkDir D:\AutoCad 运行它的 install.ps1。
   不要提权运行：它必须和 AutoCAD、SolidWorks 是同一个 Windows 用户。
3. Codex 用户：把 codex-config.snippet.toml 追加到 ~/.codex/config.toml，然后把
   D:\cad-agents\dsh-skills\solidworks-automation 和 D:\cad-agents\dsh-skills\autocad-mcp-pro
   junction 进 ~/.codex\skills\。
4. 验证：dsh --profile headless --dump-config、codex mcp list，并运行
   mcp-shim\sw_discovery_shim.py --build-catalog 重建垫片目录。
5. 不要改动 AutoCAD、SolidWorks 和注册表，除 ~/.dsh 与 ~/.codex 之外不要往 C 盘写东西。
   哪一步失败就如实报告，不要自己绕过。
```

### 前置条件

- Windows 10/11，已安装 AutoCAD 和/或 SolidWorks
- PATH 里有 Python ≥ 3.11（`python --version`）
- dsh（DeepSeek Harness）或 Codex CLI
- Git —— 可选；git 被拦时安装器会自动降级为 codeload zip 下载

先把仓库拿到本地：

```powershell
git clone https://github.com/27painter/dsh-sw-cad.git
cd dsh-sw-cad
```

### 版本支持

上游都是"按一个版本验收、按一个区间写代码"，实际情况是：

| CAD | 已验证 | 预期可用 | 依据 |
|---|---|---|---|
| AutoCAD | 2026（所有 COM 路径真机跑过）、2027（API 兼容） | 2025 / 2024 / 2023 | COM 走的是长期稳定的 ActiveX 接口。想确认你的版本，跑上游的 `scripts\smoke_*_com.py`。另外某个功能在你这版 COM 上做不了时，ezdxf 后端照样能做——它根本不需要 AutoCAD。 |
| SolidWorks | 2026 SP4.1（上游验收目标） | 2025 / 2024 / 2023 | 代码里带明确的 SW2024 修复（中文版按名称选草图、`AddComponent4` 返回 `None`），上游贡献指南要求 2020–2025 兼容。2023 没有实际跑过。 |

老版本不会被拒绝——代码里没有任何版本号检查。它们只是不在上游的验证范围内，所以**先拿一个小任务试，看过结果再信任整张图**。

### 路线 A —— 按上游原始方式安装，再注册

下面就是两个上游项目自己的安装说明。`install.ps1` 自动化的也是这些步骤，所以想理解或排查安装过程时看这一节。

**AutoCAD MCP 服务器** —— [上游安装说明](https://github.com/U-C4N/Autocad-MCP#install)。装进数据盘上的 venv
（本仓库推荐做法）：

```powershell
python -m venv D:\cad-agents\venv-autocad
D:\cad-agents\venv-autocad\Scripts\python.exe -m pip install "autocad-mcp-pro[full]"
D:\cad-agents\venv-autocad\Scripts\autocad-mcp.exe --help    # stdio 服务器入口
```

不建 venv 的话，上游自己的一行式安装：

```bash
pip install autocad-mcp-pro              # 或：uvx autocad-mcp-pro
autocad-mcp                              # 后端自动选择
AUTOCAD_MCP_BACKEND=ezdxf autocad-mcp    # 无头 DXF，不需要 AutoCAD
AUTOCAD_MCP_BACKEND=com   autocad-mcp    # 实控 AutoCAD（需要 [com] extra）
```

extras 含义：`[com]` = 实控 AutoCAD（pywin32 + Pillow），`[pdf]` = matplotlib（PDF/PNG 输出），
`[office]` = openpyxl（XLSX 算量），`[full]` = 以上全部。上游还文档化了 HTTP 传输
（`autocad-mcp --transport http --port 8000`）以及 Claude Desktop / Cursor 的 JSON 配置写法。

**SolidWorks Skill + MCP** —— [上游快速开始](https://github.com/wzyn20051216/solidworks-automation-skill#quick-start)。
上游提供三种入口，任选其一：

```bash
# 方式 1 —— 只装 Skill（最简单）
npx github:wzyn20051216/solidworks-automation-skill

# 方式 2 —— 通过 Smithery 装 MCP
npm install -g @smithery/cli
smithery mcp add wzyn20051216/solidworks-automation-skill --client codex --config '{}'

# 方式 3 —— CAD Studio 桌面版，去上游 Releases 下载
```

手动克隆（本仓库的做法；想把 skill 放数据盘就用这个）：

```powershell
git clone --depth 1 https://github.com/wzyn20051216/solidworks-automation-skill.git D:\cad-agents\dsh-skills\solidworks-automation
D:\cad-agents\venv-solidworks\Scripts\python.exe -m pip install -r D:\cad-agents\dsh-skills\solidworks-automation\requirements.txt
D:\cad-agents\venv-solidworks\Scripts\python.exe -m pip install -r D:\cad-agents\dsh-skills\solidworks-automation\mcp-server\requirements.txt
D:\cad-agents\venv-solidworks\Scripts\python.exe D:\cad-agents\dsh-skills\solidworks-automation\scripts\cad_doctor.py
```

**可选：给 SolidWorks 套上 discovery 垫片**（路线 B 会自动做）。不套的话，客户端每次请求都要背着 66 个
工具定义——23,404 token：

```powershell
New-Item -ItemType Directory -Force -Path D:\cad-agents\mcp-shim | Out-Null
Copy-Item .\mcp-shim\* D:\cad-agents\mcp-shim\ -Force

# 建检索目录（装一次，之后上游更新了再重建）
D:\cad-agents\venv-solidworks\Scripts\python.exe D:\cad-agents\mcp-shim\sw_discovery_shim.py `
  --python D:\cad-agents\venv-solidworks\Scripts\python.exe `
  --server D:\cad-agents\dsh-skills\solidworks-automation\mcp-server\server.py --build-catalog
```

**把两个服务器注册到客户端。** dsh 读 `~/.dsh/cordis.patch.yml`——把本仓库的 `cordis.patch.yml` 拷过去并按需改
路径；Codex 读 `~/.codex/config.toml`——追加 `codex-config.snippet.toml`，或者用上游自带的注册脚本
（`mcp-server\register_codex_mcp.ps1 -Python <venv 的 python> -InstallDependencies`）。两份配置都已经指向
垫片；如果你更想要直连的 66 工具服务器，两个文件里都留了注释掉的备用块。

**让 Skill 可见。** 两个客户端都只扫自己的技能目录：

```powershell
# dsh：整个 skills 目录做一个 junction（与 install.ps1 行为一致）
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.dsh" | Out-Null
New-Item -ItemType Junction -Path "$env:USERPROFILE\.dsh\skills" -Target "D:\cad-agents\dsh-skills"

# Codex：每个 skill 单独 junction（它的 skills 目录里可能还有别的技能）
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills" | Out-Null
New-Item -ItemType Junction -Path "$env:USERPROFILE\.codex\skills\solidworks-automation" -Target "D:\cad-agents\dsh-skills\solidworks-automation"
New-Item -ItemType Junction -Path "$env:USERPROFILE\.codex\skills\autocad-mcp-pro"       -Target "D:\cad-agents\dsh-skills\autocad-mcp-pro"
```

### 路线 B —— 一个脚本（本仓库）

`install.ps1` 会把路线 A 全部跑一遍，外加那些容易做错的部分：两个 venv 落在数据盘、junction 接线、
discovery 模式参数、以及 `~/.dsh/cordis.patch.yml` 的带时间戳备份。

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1

# 自定义位置 / 强制重建
.\install.ps1 -BaseDir "E:\cad" -WorkDir "E:\AutoCad" -Force
```

它依次检查 Python、建目录、建两个 venv、装 `autocad-mcp-pro[full]` 和 SolidWorks 依赖、克隆 skill
（git 被拦时自动 zip 下载）、装 discovery 垫片并生成检索目录、junction 进 `~/.dsh/skills`、写 dsh 配置、
落下 AutoCAD skill。

**Codex 用户仍需一步**——把 `codex-config.snippet.toml` 追加到 `~/.codex/config.toml`，并把两个 skill
junction 进 `~/.codex/skills/`（即路线 A 的最后四条命令）。

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

# SolidWorks 垫片：重建目录并打印工具数
& "D:\cad-agents\venv-solidworks\Scripts\python.exe" "D:\cad-agents\mcp-shim\sw_discovery_shim.py" `
  --python "D:\cad-agents\venv-solidworks\Scripts\python.exe" `
  --server "D:\cad-agents\dsh-skills\solidworks-automation\mcp-server\server.py" --build-catalog

# Codex
codex mcp list
```

发布前实际验证过的：两个 MCP 服务器都能完成 JSON-RPC 握手（`autocad-mcp-pro` v1.6.0、`solidworks_mcp`
v1.30.0）；discovery 模式只广告两个元工具，且能按关键词检索到 `gear_draw_spur_front_view` /
`keyway_draw_keyed_bore`；`dsh --dump-config` 两条注册都能加载；SolidWorks 自检通过。垫片做了端到端实测：
36 条中英文查询下 `tools/list` 是 256 token 而不是 23,404，每条查询的首位结果都正确，`call_tool` 把
`solidworks_health_check` 转发给了真实运行的 SolidWorks 2026。

## 设计说明

**1. discovery 模式是这套配置的核心。** 242 个工具定义全量广告约 8.4 万 token。discovery 模式下客户端只看到
两个工具（约 356 token），检索目录里带 206 个 AutoCAD 命令名 + 1439 个同义词——说"倒角"能找到 `FILLET`。

**2. SolidWorks 也需要同一招，所以有了垫片。** 上游服务器无条件注册全部 66 个工具，实测在线上就是 23,404
token，还没问任何问题就先付掉了。垫片只广告 `search_tools` + `call_tool`（256 token），检索走缓存目录，
真正调用工具时才拉起上游进程。`sw_lexicon.json` 里 57 组中英同义词就是"拉伸凸台"能落到
`solidworks_sketch_and_extrude`、"干涉检查"能落到 `solidworks_interference_check` 的原因。上游一行没动，
所以 skill 目录里 `git pull` 照常可用。

**3. `TOOL_PROFILE` 保持 `full`、`TOOL_PACKS` 保持 `all`。** 它们只收缩"可检索目录"，不改变空闲成本。
实测 `lean + core,mech` 会把目录砍到 58 个工具，参数化齿轮和键槽工具直接搜不到——收缩 packs 只丢能力，
一个 token 也省不下。

**4. 用 junction，不复制。** dsh 只扫 `~/.dsh/skills`，Codex 只扫 `~/.codex/skills`。目录联接把两边都指到
数据盘，两个工具都无感知。删联接不删数据。

**5. 把文件访问圈起来。** AutoCAD 服务器的 `ALLOWED_PATHS` 默认是空的，它自己的 README 也要求你设置。
本配置包把它设为一个工作目录。

**6. 分清什么验证过。** 安装章节的版本表写得很直白：上游验证过 AutoCAD 2026 和 SolidWorks 2026 SP4.1，
其余都是"预期"而非"已证明"——先拿小任务试。

**7. discovery 不是免费，只是延后。** 每次调用多一个来回：先搜再调。只画一个方块时它比直连慢，而且上游
skill 更新后没重建目录，检索结果会过期。但 CAD 任务通常一次会话里要调好几个工具，这笔账是划算的。

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| dsh 列不出 skill | `~/.dsh/skills` 必须是 junction 且目标存在；目录名必须等于 frontmatter 的 `name` |
| Codex 列不出 skill | `~/.codex/skills\` 下两个 junction 是否都在；Codex 版本需支持 skills |
| AutoCAD 工具调了没反应 | CAD 没开，或用户/完整性级别不一致；设 `AUTOCAD_MCP_BACKEND=com` 排除 ezdxf 降级 |
| SolidWorks 自检不过 | 缺依赖（装 skill 的 `requirements.txt`）或 SW 没开 |
| `search_tools` 搜出一个已经不存在的工具 | 垫片的目录过期了——重跑 `--build-catalog` |
| `call_tool` 报"Could not start the SolidWorks MCP server" | 客户端配置里的 `--python` / `--server` 路径不对，或 skill 被移动了 |
| `codex mcp list` 为空 | 追加段必须在 `config.toml` 末尾；路径要双反斜杠 |
| 安装时 `git clone` 失败 | 安装器会自动降级到 codeload zip 下载 |

## 许可证

MIT（见 [LICENSE](LICENSE)）。上游组件保持各自 MIT 许可证，本仓库不重新分发它们。
