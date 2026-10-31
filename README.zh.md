# dsh-sw-cad

[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![npm](https://img.shields.io/npm/v/dsh-sw-cad?logo=npm)](https://www.npmjs.com/package/dsh-sw-cad)
[![awesome-dsh-plugin](https://img.shields.io/static/v1?label=awesome-dsh-plugin&message=listed&color=blue)](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)

**两个 MCP 服务器 + 两个 Skill，让 DeepSeek Harness 或 Codex 的 agent 真正操控 CAD 软件。**
它们封装了两个现成的开源项目——[U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP) 和
[wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill)——
让 agent 能在 AutoCAD 里画图、在 SolidWorks 里建模，并把结果导出。本仓库是**配置层**：隔离环境、启动参数、
按需加载、客户端注册。它不 fork、不魔改、不重新分发这两个服务器。上游缺什么能力，本仓库就在它前面补一层
——现在两台服务器都自带 discovery 模式，所以本仓库不再提供垫片，只负责把它们配置起来。

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
| `autocad` | 2 | 365 token | 上游自带 `DISCOVERY_MODE=search`：只广告 `search_tools` + `call_tool`，242 个工具的目录按关键词检索 |
| `solidworks` | 3 | 474 token | 上游 `SW_MCP_DISCOVERY=search`：`sw_search_tools` / `sw_describe_tool` / `sw_call_tool`；66 个工具的目录（50 个 `solidworks_*` + 14 个 `cadstudio_*` + 2 个 `design_*`）按需检索，全量广告要 23,411 token |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/token-cost-dark.svg">
  <img alt="常驻上下文成本柱状图：AutoCAD 直连 83,732 token，SolidWorks 直连 23,411，两个都直连 107,143，本仓库配置 839" src="docs/token-cost-light.svg">
</picture>

*工具定义是在你提出第一个请求之前就要付掉的。两台服务器直连要 107,143 token，走本仓库这套配置是 839，
省 99.2%。两边用的是同一种测法：线上 `tools/list` 的字节数，用 `cl100k_base` 计数。*

```
你说人话
   │
   ├─ dsh / Codex 按 SKILL.md 的 description 按需加载 Skill
   │      ├─ solidworks-automation  → sw_*.py 脚本 → COM → SolidWorks
   │      └─ autocad-mcp-pro        → search_tools / call_tool → MCP → AutoCAD
   │
   └─ MCP 服务器（cordis.patch.yml / codex config.toml）
          ├─ autocad    上游 discovery 模式，242 工具按需检索
          └─ solidworks 上游 discovery，3 个入口工具，66 工具按需检索
                          └─ 首次调用时才拉起上游 server.py
```

## 哪些是上游的，哪些是本仓库的

这一点在这里比通常更重要——两个服务器都是别人的成果。分工如下：

| 部分 | 来源 | 许可证 |
|---|---|---|
| AutoCAD MCP 服务器（`autocad-mcp-pro`） | [U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP)，从 PyPI 安装 | MIT |
| SolidWorks Skill + MCP 服务器 | [wzyn20051216/solidworks-automation-skill](https://github.com/wzyn20051216/solidworks-automation-skill)，安装时克隆 | MIT |
| `skills/autocad-mcp-pro/SKILL.md` | 本仓库原创——上游没有 AutoCAD skill | MIT |
| `bin/`、`lib/`（含探测 CAD COM 服务器的 `probe.py`）、`package.json`、`cordis.patch.yml`、`install.ps1`、`bootstrap.ps1`、`codex-config.snippet.toml`、`assets/`、两份 README | 本仓库原创 | MIT |

**上游代码一行都没有进仓库。** 安装脚本在运行时才去 PyPI 取包、克隆（或 zip 下载）SolidWorks 仓库，所以
你拿到的永远是上游当前版本，著作权也始终归上游。本仓库补的是它们缺失的那一层：经过验证的启动参数、
两台服务器的 discovery 模式配置、按需 Skill 接线、把几个 G 的依赖挡在 C 盘之外的
junction 技巧，以及一个在 git 协议被拦时还能装完的安装器。

## 安装

三条路，挑一条。装之前先看本节末尾的前置条件。

### 1. 作为 dsh 插件安装（推荐）

```bash
dsh plugin --profile web add dsh-sw-cad
```

这就是全部。`package.json` 里声明了 `dsh.bundle.patch`，dsh 会读到本仓库的 `cordis.patch.yml`，
它只插入一行：这个包本身。包是一个真正的 cordis 插件（`lib/index.js`），不是包着一个可执行文件的壳，
所以 dsh 直接从 profile 里 import 它，不需要任何东西在 PATH 上。

首次加载时插件会自己解析路径，缺上游就把**钉死版本**的上游装好（几分钟，只一次），然后注册两个 MCP
客户端（绝对路径）。装不上就直接抛错，带上缺失的路径和重跑的命令——而不是注册两行起不来的配置，
让人以为装好了却没有工具。设 `SW_CAD_NO_AUTO_INSTALL=1` 可以关掉自动安装，把缺失项变成一条明确的报错。

### 2. npm 全局安装（附带 CLI）

```bash
npm install -g dsh-sw-cad
dsh-sw-cad install
```

结果一样，额外把 `dsh-sw-cad` 放进 PATH，可以用 `doctor`、`where`、`serve`——不想启动 dsh 就想先查一下
装没装好时有用。

### 3. 不用 npm：`bootstrap.ps1`

```powershell
git clone https://github.com/27painter/dsh-sw-cad.git
cd dsh-sw-cad
.\bootstrap.ps1 -Check     # 只检测，不改动；缺东西时退出码为 1
.\bootstrap.ps1            # 缺什么装什么
```

它检查十项，只修缺失或过期的部分：

| 检查项 | 由谁修复 |
|---|---|
| Python ≥ 3.11 | 不修——报出问题就停 |
| `venv-autocad` + `autocad-mcp-pro` | `install.ps1` |
| `venv-solidworks` + `mcp` / `pymupdf` / `ezdxf` / `pywin32` | `install.ps1` |
| SolidWorks skill 克隆 | `install.ps1` |
| `~/.dsh/skills` junction | 创建 |
| `~/.dsh/cordis.patch.yml` 条目 | `install.ps1`（先备份） |
| `~/.codex/skills` junction | 创建 |
| `~/.codex/config.toml` 条目 | 追加（先备份） |

跑第二次是空操作。全部就绪时退出码 `0`，否则 `1`，所以可以放心塞进脚本或交给 agent。
除非核心组件真的缺失，否则不会下载任何东西。

> [!WARNING]
> 第 3 条路还会写 `~/.dsh/cordis.patch.yml`。如果你同时又装了插件，那就是用同一个 `serverName` 把同样
> 两个服务器注册了第二遍，dsh 会拒绝重复注册，会话直接起不来。两条路只能走一条：装了插件就把
> `~/.dsh/cordis.patch.yml` 留成 `[]`。

### 命令一览

| 命令 | 作用 |
|---|---|
| `dsh-sw-cad install` | 装钉死版本的上游服务器，并写 `~/.dsh/sw-cad.json` |
| `dsh-sw-cad doctor` | 报告哪些有、哪些缺，并打印钉死的版本；不改动，缺东西时退出码 1 |
| `dsh-sw-cad where` | 打印服务器会用到的每一条路径 |
| `dsh-sw-cad serve autocad\|solidworks` | 以 stdio 跑一个 MCP 服务器，用于手动测试 |

`install` 是幂等的，而且插件首次加载时会自动跑一遍。版本钉在 `lib/upstream.json`——`autocad-mcp-pro` 1.6.0
加一个固定的 SolidWorks skill 提交——所以今天装和一个月后装，拿到的是同一套服务器。

> [!TIP]
> **不想自己敲这些命令？** 把下面这段发给你的 agent（dsh、Codex，或任何能在这台机器上跑命令的 agent），
> 它会替你装完——后面几节只在出问题时才需要看。

```text
在这台 Windows 机器上安装 https://github.com/27painter/dsh-sw-cad。

运行：npm install -g dsh-sw-cad && dsh-sw-cad install
第一条装包，第二条装两个上游服务器（AutoCAD MCP 与 SolidWorks skill）并写好客户端条目。
dsh-sw-cad install 是幂等的，只补缺失的部分。

不要提权运行：它必须和 AutoCAD、SolidWorks 是同一个 Windows 用户。
除 ~/.dsh 与 ~/.codex 之外不要往 C 盘写东西，也不要动 CAD 安装目录和注册表。
装完用 dsh-sw-cad doctor、dsh --profile headless --dump-config、codex mcp list 验证。
哪一步失败就如实报告，不要自己绕过。
```

### 前置条件

- Windows 10/11，已安装 AutoCAD 和/或 SolidWorks
- PATH 里有 Python ≥ 3.11（`python --version`）
- dsh（DeepSeek Harness）或 Codex CLI；第 1、2 条路还需要 Node ≥ 20
- Git —— 可选；git 被拦时安装器会自动降级为 codeload zip 下载

先把仓库拿到本地：

```powershell
git clone https://github.com/27painter/dsh-sw-cad.git
cd dsh-sw-cad
```

### 机器上没装 CAD 应用会怎样

注册之前插件会先查两个 COM 服务器（`lib/probe.py`，查注册表 ProgID，不会启动任何一个应用）。
只装了 AutoCAD 就只注册 AutoCAD 那一行，只装了 SolidWorks 就只注册那一行；两个都没装就一行都
不注册，并在日志里说明——而不是放两个只能失败的工具在那里。

```
[dsh-sw-cad] solidworks: no COM server registered on this machine, not registering it
[dsh-sw-cad] registered autocad (base D://cad-agents, upstream pinned in lib/upstream.json)
```

`SW_CAD_DISABLE=autocad,solidworks` 可以把这个判断改成显式设置，`dsh-sw-cad doctor` 会在不启动
dsh 的情况下打印探测结果。

另外，**装过**不等于**开着**。AutoCAD 注册过但没启动，工具会一直等应用响应，最后报一条“可能有模态
对话框”的错；SolidWorks 的 health_check 报的是装没装，不是开没开。所以把 CAD 应用以与 agent 同一个
Windows 用户启动，是安装之后最后要做的一步。


### 版本支持

上游都是"按一个版本验收、按一个区间写代码"，实际情况是：

| CAD | 已验证 | 预期可用 | 依据 |
|---|---|---|---|
| AutoCAD | 2026（所有 COM 路径真机跑过）、2027（API 兼容） | 2025 / 2024 / 2023 | COM 走的是长期稳定的 ActiveX 接口。想确认你的版本，跑上游的 `scripts\smoke_*_com.py`。另外某个功能在你这版 COM 上做不了时，ezdxf 后端照样能做——它根本不需要 AutoCAD。 |
| SolidWorks | 2026 SP4.1（上游验收目标）、**2025 SP05**（真机跑过：零件 → 草图 → 矩形 → 拉伸 → 保存 → STEP） | 2024 / 2023 | 代码里带明确的 SW2024 修复（中文版按名称选草图、`AddComponent4` 返回 `None`），上游贡献指南要求 2020–2025 兼容。2025 那次跑出来的草图名是中文 `草图1`，所以那段兼容路径也被实际走到了。2023 没有实际跑过。 |

老版本不会被拒绝——代码里没有任何版本号检查。它们只是不在上游的验证范围内，所以**先拿一个小任务试，看过结果再信任整张图**。

**这个包自己实际跑过什么。** 下面的真机验证是 2026 年 10 月在 AutoCAD 2026 和 SolidWorks 2026 SP4.1 上做的。
之后又单独跑过 **SolidWorks 2025 SP05**（零件 → 草图 → 矩形 → 拉伸 → 一个实体 → 保存 → STEP，草图名是中文），
所以 2025 现在也算验证过了；AutoCAD 这边只跑过 2026 一个版本。

### 附录 —— 上游自己的安装步骤

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

**给 SolidWorks 服务器设 `SW_MCP_DISCOVERY=search`**（上面三条路都会自动做，这里是手动步骤）。上游从
2026-10-29 起默认就是这个值，所以没配过的客户端本来就会拿到三个入口工具；显式设一下，是为了万一上游
把默认改回去时仍然成立。不设的话，客户端每次请求都要背着 66 个工具定义——23,411 token。

**把两个服务器注册到客户端。** dsh 读 `~/.dsh/cordis.patch.yml`——把本仓库的 `cordis.patch.yml` 拷过去即可。
它只插入一行——这个包本身——由插件自己去注册服务器，所以不需要任何东西在 PATH 上
（`npm install -g dsh-sw-cad`）。Codex 读 `~/.codex/config.toml`——追加 `codex-config.snippet.toml`，或者用
上游自带的注册脚本（`mcp-server\register_codex_mcp.ps1 -Python <venv 的 python> -InstallDependencies`）。
Codex 那份里是绝对路径，要按你的 `-BaseDir` 改。

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

### 附录 —— `install.ps1` 干了什么

`install.ps1` 会把上面附录里的事全跑一遍，外加那些容易做错的部分：两个 venv 落在数据盘、junction 接线、
discovery 模式参数、以及 `~/.dsh/cordis.patch.yml` 的带时间戳备份。

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1

# 自定义位置 / 强制重建
.\install.ps1 -BaseDir "E:\cad" -WorkDir "E:\AutoCad" -Force
```

它依次检查 Python、建目录、建两个 venv、装 `autocad-mcp-pro[full]` 和 SolidWorks 依赖、克隆 skill
（直接下固定提交的 zip，不需要 git）、junction 进 `~/.dsh/skills`、写 dsh 配置、
落下 AutoCAD skill。

不确定机器上已经装了什么的话，用 `bootstrap.ps1`：它自己会判断需不需要跑 `install.ps1`。

**Codex 用户仍需一步**——把 `codex-config.snippet.toml` 追加到 `~/.codex/config.toml`，并把两个 skill
junction 进 `~/.codex/skills/`（即附录里的最后四条命令）。

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

- **SolidWorks 的工具参数要多套一层。** 上游工具把参数放在 `params` 键下面，所以调用长这样：
  `call_tool(name="solidworks_sketch_and_extrude", arguments={"params": {"shape": "rectangle",
  "width_mm": 60, "height_mm": 40, "depth_mm": 10}})`。AutoCAD 的工具是平铺的。写错了会报
  pydantic 的 `params Field required`，看到那个就知道是这回事。
- **一次只说一个 CAD。** 同时提两个会加载两个 Skill，任务也容易混。
- **复杂任务先要计划。** 两个 Skill 都有规划环节；在画几何之前先审计划，比画错了再撤销便宜。
- **主动要预览。** AutoCAD 路线最后有 8 步验证器加截图；SolidWorks 路线会导出四张正交预览图。
  说一句"把预览图给我看"是合理要求，Skill 会照做。
  一个实测过的注意事项：AutoCAD 的截图需要窗口可见——隐藏或最小化时拿回来是空白图。几何本身始终可以
  通过 API 验证（handle、包围盒、质量属性），所以别把空白截图当成画错了。

## 验证

```powershell
# 该装的都装了吗？只读检测，缺东西时退出码 1
dsh-sw-cad doctor

# 服务器会用到的每一条路径
dsh-sw-cad where

# dsh 是否看见两个服务器、两个技能
pnpm dsh --profile headless --dump-config
pnpm dsh --profile headless "列出当前可用的 skill"

# SolidWorks 技能自检（Python 依赖 + COM）
& "D:\cad-agents\venv-solidworks\Scripts\python.exe" "D:\cad-agents\dsh-skills\solidworks-automation\scripts\sw_preflight.py"

# 用 dsh 实际调用的同一个入口，验证两个服务器都能握手
node bin\dsh-sw-cad.mjs serve solidworks     # 应为 solidworks_mcp，3 个入口工具
node bin\dsh-sw-cad.mjs serve autocad        # 应为 AutoCAD MCP Pro，2 个入口工具

# Codex
codex mcp list
```

发布前实际验证过的（AutoCAD 2026 与 SolidWorks 2026 都在运行）：`system_status` 返回
`connected: true`、`backend: com`、运行中的版本号和当前活动文档名；`drawing_info` 返回打开文档的实体数、
范围与单位；`layer_list` 返回实时的图层表；SolidWorks 侧返回 `status: ok` 并识别到正在运行的实例。
两个 `serve` 目标也都能通过包自带的入口完成 JSON-RPC 握手——AutoCAD 服务器自报
`AutoCAD MCP Pro 3.4.11`（pip 包是 1.6.0，版本号在服务器侧是另一套），SolidWorks 侧自报
`solidworks_mcp` 1.30.0——两边分别广告 2 个和 3 个入口工具。discovery 能按关键词检索到
`gear_draw_spur_front_view` / `keyway_draw_keyed_bore`；`dsh --dump-config` 两条记录都能加载且命令为
`dsh-sw-cad`；SolidWorks 自检通过。SolidWorks 侧做了端到端实测：
`sw_search_tools` 用中文「健康检查」能命中 `solidworks_health_check`，`sw_call_tool` 把它送到运行中的
`solidworks_health_check` 转发给了真实运行的 SolidWorks 2026。

## 设计说明

**1. discovery 模式是这套配置的核心。** 242 个工具定义全量广告是 83,732 token。discovery 模式下客户端只看到
两个工具（365 token），检索目录里带 206 个 AutoCAD 命令名 + 1439 个同义词——说"倒角"能找到 `FILLET`。

**2. SolidWorks 那侧以前需要垫片，后来上游自己做了。** 2026-10-29 之前，那台服务器无条件注册全部 66 个
工具，线上实测就是 23,411 token，还没问任何问题就先付掉了，所以本仓库当时带了一个只广告两个元工具的垫片。
后来上游自己实现了同一件事，做得更完整，本仓库就切了过去：`SW_MCP_DISCOVERY=search` 暴露
`sw_search_tools` / `sw_describe_tool` / `sw_call_tool`（474 token），中英同义词按概念计分，而且
`sw_describe_tool` 能按需返回单个 schema——这是垫片做不到的。垫片被删掉了，没有留成第二份实现：
一个功能一旦由它的上游自己维护，再并行维护一份只会慢慢漂走。

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

**8. 它是插件，不是 patch 调一个可执行文件。** 第一版注册的两行 MCP 客户端，`command` 写的是
`dsh-sw-cad`——只有全局安装过才解析得到，于是单独跑 `dsh plugin add` 会留下两行起不来的配置。评审指出
了这一点，指得对。现在 `lib/index.js` 本身就是一个 cordis 插件，由它自己用绝对路径注册两个客户端，
PATH 依赖没了，也才有可能在注册之前先检查上游。

**9. 钉死版本，因为"最新"不是一个版本号。** `lib/upstream.json` 记录了这套配置验证时的确切
`autocad-mcp-pro` 版本和确切的 SolidWorks skill 提交；`install.ps1` 的 pip 安装和压缩包下载都用它。
改它就是一次刻意的提交：发布前要确认 `SW_MCP_DISCOVERY=search` 仍然只暴露那三个入口工具。

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| dsh 列不出 skill | `~/.dsh/skills` 必须是 junction 且目标存在；目录名必须等于 frontmatter 的 `name` |
| Codex 列不出 skill | `~/.codex/skills\` 下两个 junction 是否都在；Codex 版本需支持 skills |
| AutoCAD 工具调了没反应 | CAD 没开，或用户/完整性级别不一致；设 `AUTOCAD_MCP_BACKEND=com` 排除 ezdxf 降级 |
| SolidWorks 自检不过 | 缺依赖（装 skill 的 `requirements.txt`）或 SW 没开 |
| `sw_search_tools` 搜不出有用的东西 | 服务器跑在 `full` 模式，没有检索入口——检查 `SW_MCP_DISCOVERY` |
| `call_tool` 报"Could not start the SolidWorks MCP server" | 客户端配置里的 `--python` / `--server` 路径不对，或 skill 被移动了 |
| `codex mcp list` 为空 | 追加段必须在 `config.toml` 末尾；路径要双反斜杠 |
| 安装时 `git clone` 失败 | 安装器会自动降级到 codeload zip 下载 |

## 许可证

MIT（见 [LICENSE](LICENSE)）。上游组件保持各自 MIT 许可证，本仓库不重新分发它们。
