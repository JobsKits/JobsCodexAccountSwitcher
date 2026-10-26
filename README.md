# `JobsCodexAccountSwitcher`

![Jobs出品，必属精品](https://picsum.photos/1500/400)

<!-- jobs-toc:start -->
<!-- jobs-toc:source {"v":2,"token":"[toc]"} -->
<details>
<summary>📚 目录（点击展开 / 收起）</summary>

- [🔥 前言](#jobs-toc-heading-1)
- [一、功能与工程归属](#features)
- [二、目录与入口](#layout)
- [三、账户设置与操作](#account-operations)
- [四、开发与打包](#development)
  - [【MacOS】📦生成dmg.command](#jobs-toc-heading-6)
  - [【Windows】📦生成exe.bat](#jobs-toc-heading-7)
- [五、数据、日志与恢复](#data-recovery)
- [六、验证与常见问题](#verification)
- [七、本仓目录与脚本入口](#repository-layout-and-entrypoints)

</details>
<!-- jobs-toc:end -->

---

## <span id="jobs-toc-heading-1" data-jobs-toc="v2">🔥 <font id=前言>前言</font></span><!-- jobs-toc:heading-end -->

**最低支持系统：[Windows](https://www.microsoft.com/windows) 10 64 位（x64）。** 这是当前最低支持目标；具体最低 build 尚未锁定，完整实机兼容性仍待验证。

使用 [**Python**](https://www.python.org/) 和 [**PySide6**](https://doc.qt.io/qtforpython-6/) 编写的 [**macOS**](https://www.apple.com/macos/) / [**Windows**](https://www.microsoft.com/windows) 账户切换工具。通过保存、恢复 [**Codex**](https://openai.com/codex) 的文件登录缓存并重启桌面应用，减少重复浏览器授权。添加账户在独立临时 `CODEX_HOME` 中授权，不覆盖当前桌面登录文件。

[**Windows**](https://www.microsoft.com/windows) **兼容范围：当前构建与成品测试目标为 Windows 10 / 11 x64；Windows XP、Vista、7、8、8.1 不支持当前图形依赖。** [**Qt 6.8 官方 Windows 平台表**](https://doc.qt.io/qt-6.8/windows.html)列出的 x64 基线为 Windows 10 1809+ / Windows 11；本项目未锁定具体运行库版本，不能据此承诺每次构建都兼容同一最低系统版本。

成品已包含 [**Python**](https://www.python.org) 与图形依赖，运行成品无需另外安装 Python；源码运行和打包才需要开发依赖。EXE 架构随构建所用 Python 和依赖 wheel 决定，x86 / ARM64 尚无统一交付验收；Windows 原生界面与分发仍须在对应目标机验证。

构建要求为 `Python>=3.11`、`PySide6>=6.8,<7`、`PyInstaller>=6.10,<7`，另含账户加密与进程依赖，详见[依赖配置](./JobsCodexAccountSwitcher/pyproject.toml)。Windows 使用 `onedir` 分发，保留 EXE 与 `_internal/` 完整目录，不能只拷 EXE。

Token 上下行监控由同级 [JobsCodexTokenMonitor](../JobsCodexTokenMonitor.py/README.md) 独立提供；本工程只提供账户管理，不包含用量监控或钩子配置功能。启动器保留旧钩子的退役应答，避免已运行会话因缓存旧命令而报错。

**兼容条件：桌面应用必须实际使用所选 Codex 数据目录中的 `auth.json`，凭据存储为 `file`。文件切换不等于桌面授权成功，真实双账户端到端切换仍需实机核验。** `keyring` / `auto` / `ephemeral` 拒绝账户操作。默认免口令，开启保护后账户库加密；不自动退出登录或撤销授权。

入口导航：[功能](#features) · [账户操作](#account-operations) · [开发与打包](#development) · [数据与恢复](#data-recovery) · [验证](#verification)。

## 一、<span id="features">功能与工程归属</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

| 功能 | 行为 |
| --- | --- |
| 保存账户 | 同一账户更新最新缓存，名称冲突拒绝覆盖 |
| 可选口令保护 | 默认免口令；开启后使用 Scrypt / Fernet 加密，关闭前确认明文风险 |
| 切换 | 关闭所选桌面应用，保存当前最新凭据，原子替换目标缓存并重启 |
| 账户列表 | 双击切换、右键改备注或刷新，列表支持滚动 |
| 进程保护 | 其它 CLI / IDE 客户端仍运行时拒绝写入 |
| 三态主题 | 白天 / 黑夜 / 跟随系统，保存选择，监听系统外观变化 |

直接父仓 `../.gitmodules` 的真实登记如下：

```ini
[submodule "JobsCodexAccountSwitcher"]
    path = JobsCodexAccountSwitcher.py
    url = ../JobsCodexAccountSwitcher.git
```

| 父仓地址 | 子仓解析地址 |
| --- | --- |
| `git@github.com:JobsKits/JobsPythonTools.git` | `git@github.com:JobsKits/JobsCodexAccountSwitcher.git` |
| `git@gitee.com:JobsGo/JobsPythonTools.git` | `git@gitee.com:JobsGo/JobsCodexAccountSwitcher.git` |

`path` 是本地目录，`url` 是真实仓名。相对地址跟随父仓线路，前提是该平台已有子仓和父仓锁定的提交；仅新增 remote 不会切换下载线路。当前本地 `main` 对应 GitHub `origin/main` 和 Gitee `gitee/main`；不额外创建平台分支。

## 二、<span id="layout">目录与入口</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

```text
JobsCodexAccountSwitcher.py/
├── README.md
├── icon.png
├── 【MacOS】📦生成dmg.command
├── 【Windows】📦生成exe.bat
├── dist/YYYY.MM.DD HH-mm-ss/
│   ├── JobsCodexAccountSwitcher.app         # macOS
│   ├── JobsCodexAccountSwitcher/            # Windows 完整运行目录
│   │   ├── JobsCodexAccountSwitcher.exe
│   │   └── _internal/
│   └── JobsCodexAccountSwitcher.dmg / .zip
└── JobsCodexAccountSwitcher/
    ├── pyproject.toml
    ├── src/jobs_codex_account_switcher/
    │   ├── app.py / core.py / platforms.py
    │   └── appearance.py
    ├── scripts/
    └── tests/
```

第一层 APP / DMG 相对链接或 EXE / ZIP 快捷方式指向当前真实产物。APP 自带 Python 和依赖；Windows ZIP 包含完整运行目录，分发时保留 EXE 与 `_internal/`，不能只复制 EXE。不需要 Token 工具，macOS 与 Windows 分别在对应系统构建。

## 三、<span id="account-operations">账户设置与操作</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

1、运行切换器，确认 Codex 数据目录与桌面程序路径。默认读取 `CODEX_HOME`，未设置时使用当前用户的 `.codex`。macOS 按 Bundle ID 发现应用，兼容桌面应用更名为 `ChatGPT.app`；Windows 提供候选安装路径和手动选择 EXE。

2、默认自动打开免口令账户库，直接添加或保存账户即可。需要加密时勾选“启用口令保护”，设置并确认至少 12 字符的口令；开启后每次运行需解锁。旧加密库先输入原口令，解锁后取消勾选即可关闭保护，已有账户会保留。口令不保存，忘记后无法解密已有加密库。关闭保护时不再要求口令，账户凭据以明文保存；不要把账户库或真实登录文件提交到 Git。

3、在 Codex 正常登录账户 A，结束任务并完整退出桌面应用、CLI 和 IDE 后端。点击“保存当前登录账户”，在“账户备注”中填写 A。

4、选择“添加账户（独立浏览器授权）”，在“账户备注”中填写 B，在打开的浏览器中选择另一个 Google 账户并授权。此流程需要原生 Codex CLI；在应用数据目录的独立临时目录完成授权，按当前口令保护模式保存后删除临时明文，不调用退出登录、不覆盖当前桌面登录文件。macOS 自动发现桌面应用内置 CLI，Windows 可手动选择 `codex.exe`，不使用 npm 的 `.cmd`。CLI 不可用时，仍可手动在 Codex 登录 B 后完整退出并保存；退出登录可能撤销原会话，需要重新授权。

5、双击 A 或 B，或右键目标账户选择“切换账户并启动 Codex”。确认后自动关闭所选应用、切换缓存并重启。正常退出失败时会终止该应用残留进程，运行中的任务会中断，请先保存工作。启动失败可调整应用路径后手动启动；切换前的账户凭据已同步保存到列表。

6、在桌面应用检查目标邮箱、工作区并正常发起任务，无需返回工具点击核验成功。需要切回时，再双击原账户即可。

“完整退出”不等于关闭窗口；Mac 使用退出应用，Windows 退出窗口及后台进程。任务、云端聊天、连接、远程配对、插件权限与订阅可能属于账户或工作区，工具不迁移这些数据，不保证切换后仍可使用原账户资源。

在账户列表中右键目标账户，选择“更改备注”。输入框预填原备注，确定后立即保存并保持选中；取消不修改。备注为 1～80 个字符，不能与其它账户重复。此操作不需要退出 Codex，也不会修改登录凭据。账户行及列表空白处的右键菜单均提供“刷新缓存状态”；账户列表支持鼠标滚轮、触控板和垂直滚动条，账户多时仍可上下查看。

## 四、<span id="development">开发与打包</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

| 入口 | 环境、操作与输出 |
| --- | --- |
| `./【MacOS】📦生成dmg.command` | 内置 Homebrew / Python 自检，构建账户 APP / DMG |
| `./【Windows】📦生成exe.bat` | 检查 Python 与 PowerShell，构建账户 EXE / ZIP |
| `./JobsCodexAccountSwitcher/scripts/build.py` | 内层构建器，同样要求阅读自述并确认 |

需要 Python 3.11+，依赖为 PySide6、[**cryptography**](https://cryptography.io/) 和 [**psutil**](https://psutil.readthedocs.io/)，构建另需 [**PyInstaller**](https://pyinstaller.org/)。健康依赖复用；缺少必需依赖时直接回车安装，任意非空输入、EOF 或非交互均取消；失败立即停止。

macOS 入口在自己的具名函数中封装完整环境自检，不依赖其它工程或外置环境库。已有 [**Homebrew**](https://brew.sh/) 执行 `brew update` 更新索引，不全量升级软件；缺失时精确输入 `YES` 选择系统安装目录，其它输入或回车选择用户目录。Python 通过 Brew 管理，满足版本与标准库要求则复用，不满足才修复并复检；不兼容的旧虚拟环境改名备份后重建。新系统缺少 Command Line Tools 时按系统提示完成安装后重试。

开始构建前提示并确认清理本工程固定 `./dist/` 的旧产物；拒绝外部或符号链接目录。不会清理账户库、源码或其它工具。产物使用 `YYYY.MM.DD HH-mm-ss` 时间目录，成功后发布第一层入口、打开产物位置并启动账户 APP / EXE；成功结尾无需回车，失败不启动旧包。包未正式签名 / 公证。

macOS 源码运行：

```shell
python3 -m venv ./JobsCodexAccountSwitcher/.venv
./JobsCodexAccountSwitcher/.venv/bin/python -m pip install \
    -e ./JobsCodexAccountSwitcher
./JobsCodexAccountSwitcher/.venv/bin/python \
    ./JobsCodexAccountSwitcher/scripts/launch.py
```

Windows PowerShell：

```powershell
& .\JobsCodexAccountSwitcher\.venv\Scripts\python.exe `
    .\JobsCodexAccountSwitcher\scripts\launch.py
```

`--codex-home PATH` 指定数据目录；未指定时读取 `CODEX_HOME`，再回退当前用户的 `.codex`。Token 参数已移到独立监控工程，旧命令应改用独立工具。

已打开的客户端可能继续执行旧钩子：旧 `--hook-report` 只输出合法 JSON 迁移提示并退出，旧 `--hook-launch` 静默退出。这两条退役路径只使用标准库，不读取会话、账户或输入事件，不启动 / 调用监控工具，也不打开账户窗口。监控 GUI、安装或移除钩子的参数仍拒绝；新钩子只应指向独立监控 APP。

### <span id="jobs-toc-heading-6" data-jobs-toc="v2">[【MacOS】📦生成dmg.command](./【MacOS】📦生成dmg.command)</span><!-- jobs-toc:heading-end -->

> **目的：** 在本机生成 JobsCodexAccountSwitcher 的 APP / DMG，并打开本次成品。

* 阅读自述，确认环境准备；检查 Homebrew 和兼容的 Python，再交给本工程构建器。
  * 必需依赖缺失：回车安装，任意非空输入取消；环境或复检失败时停止。
* 确认构建范围后准备虚拟环境与依赖，仅清理本工具外层 `dist/` 的旧产物。
* 打包并校验 APP，封装 DMG，统一输出到 `dist/YYYY.MM.DD HH-mm-ss/`。
* 更新第一层产物快捷方式，打开产物目录，再启动本次 APP；失败时查看系统临时目录中的构建日志。

### <span id="jobs-toc-heading-7" data-jobs-toc="v2">[【Windows】📦生成exe.bat](./【Windows】📦生成exe.bat)</span><!-- jobs-toc:heading-end -->

> **目的：** 在 Windows 本机生成 JobsCodexAccountSwitcher 的 EXE 分发目录与 ZIP。

* 显示构建说明，依次检查 `py -3` 和 `python`，转交本工程构建器。
  * 没有可用 Python 3.11+：提示安装并停止；`--help` 只显示说明。
* 确认后准备项目虚拟环境；必需依赖缺失时回车安装，任意非空输入取消。
* 清理本工具外层 `dist/`，生成并核验 EXE 目录和 ZIP，保存到同一构建时间目录。
* 更新 EXE / ZIP 快捷方式，打开产物目录并启动新 EXE；失败时停止并保留日志。

## 五、<span id="data-recovery">数据、日志与恢复</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

- `accounts.enc`：系统应用数据目录 `Jobs/CodexAccountSwitcher` 中的账户库，保留原位置与格式。免口令时为明文，开启口令后加密；忘记口令无法解密。重新打包与功能拆分不迁移或清空账户数据。
- [**Qt**](https://www.qt.io/) `QSettings`：保留原账户工具的主题、数据目录和应用路径；不保存口令。
- 构建日志：系统临时目录中的 `JobsCodexAccountSwitcher-build.log`；终端显示依赖与构建进度。
- `enroll-*` 临时授权目录：正常完成或取消后删除。崩溃后确认工具退出再人工清理；删除本地文件不会撤销服务器授权。
- 实例与操作锁：`instance.lock` 和所选 Codex 目录中的 `.jobs-account-switch.lock`；确认无相关进程后再处理异常残留，不能并发切换时删锁。

JWT 仅用于本地身份匹配，不进行服务器验签，也不代表授权仍有效。不要把账户库、`auth.json` 或其它凭据提交入库、上传或写进日志。

## 六、<span id="verification">验证与常见问题</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

测试使用临时目录和虚拟授权，覆盖账户保存、加密、可选口令、身份冲突、原子写入、符号链接、进程保护、授权录入和三态主题。账户窗口只提供账户管理；命令行拒绝监控参数，缓存旧钩子的会话收到退役应答。macOS ARM64 成品已核对打包结构；真实双账户授权与 Windows 原生界面 / 打包仍需对应环境验证。

```shell
cd ./JobsCodexAccountSwitcher
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
    -m unittest discover \
    -s tests \
    -v
```

**为什么切换要关闭桌面应用？** 应用或后端可能持有旧凭据并刷新写回。工具确认进程停止后再替换；当前 Codex 任务会中断，先保存工作。

**缓存匹配但桌面没有切换？** 桌面可能使用独立登录层或受管策略；重新正常授权，工具不绕过认证。

**Token 监控去哪了？** 使用独立 [JobsCodexTokenMonitor](../JobsCodexTokenMonitor.py/README.md)，在主面板直接切换“窗口置顶”，从 `⋯ → 设置…` 控制外观和联动。

## 七、<span id="repository-layout-and-entrypoints">本仓目录与脚本入口</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

以下为当前检出的源码与维护入口；依赖环境、Git 元数据、构建缓存和生成产物略去，独立子仓仅列目录。

```text
.
├── .gitignore
├── 【MacOS】📦生成dmg.command
├── 【Windows】📦生成exe.bat
├── assets/
├── icon.png
├── JobsCodexAccountSwitcher/
│   ├── JobsCodexAccountSwitcher.py/
│   ├── pyproject.toml
│   ├── scripts/
│   ├── src/
│   └── tests/
└── README.md
```

* `JobsCodexAccountSwitcher/src/` 保存账户管理实现；`scripts/` 保存构建、启动和快捷方式辅助代码，`tests/` 保存回归用例，`assets/` 与图标目录保存资源。

<a id="🔚" href="#前言" style="font-size:17px; color:green; font-weight:bold;">我是有底线的➤点我回到首页</a>
