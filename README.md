# `JobsCodexAccountSwitcher`

![Jobs出品，必属精品](https://picsum.photos/1500/400)

[toc]

---

## 🔥 <font id=前言>前言</font>

使用 [**Python**](https://www.python.org/) 和 [**PySide6**](https://doc.qt.io/qtforpython-6/) 编写的 Windows / macOS 图形账户切换器。通过保存、恢复 [**Codex**](https://openai.com/codex) 的文件登录缓存并重启桌面应用，减少双账户反复浏览器授权。提供独立临时 `CODEX_HOME` 浏览器授权入口，添加账户不覆盖当前桌面登录文件。不修改 Codex 本体，不读取 Google 密码，不自动执行退出登录或撤销 Token。

**兼容条件：桌面应用必须实际使用所选 Codex 数据目录中的 `auth.json`，且凭据存储为 `file`。文件切换不等于桌面授权成功；真实双账户端到端切换尚未验证。** `keyring` / `auto` / `ephemeral` 明确拒绝操作，工具不会擅自改配置。

![Snipaste_2026-10-03_08-29-21](./assets/Snipaste_2026-10-03_08-29-21.png)

## 一、功能与边界 <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

| 功能 | 行为 |
| --- | --- |
| 保存账户 | 按本地身份区分账户；同一账户更新最新凭据；名称冲突拒绝覆盖 |
| 可选口令保护 | 默认免口令、明文保存；开启后全库使用 Scrypt 派生密钥和 Fernet 加密，包含名称、邮箱、Token |
| 切换 | 自动关闭所选应用，保存当前最新凭据，再原子替换目标缓存并重启应用 |
| 核验 | 本地文件匹配只标记“当前缓存”；桌面核验由用户确认 |
| 进程保护 | 按所选应用路径正常退出并等待，必要时清理其残留子进程；其它 CLI / IDE 客户端仍运行时拒绝写入 |
| 主题 | 窗口右上角紧凑下拉框，白天 / 黑夜 / 跟随系统；保存选择，跟随系统监听外观变化 |

JWT 解析仅用于本地账户匹配，不进行服务器验签，也不代表 Token 仍有效。会话过期、撤销、工作区策略变化时仍需重新浏览器授权。Mac 文件权限使用仅当前用户读写；Windows 依赖用户配置目录继承的 ACL，恢复后的 `auth.json` 是 Codex 所需的明文文件。

## 二、目录与入口 <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

```text
./
├── README.md
├── 【MacOS】📦生成dmg.command
├── 【Windows】📦生成exe.bat
├── dist/YYYY.MM.DD HH-mm-ss/      # 本机生成
├── work/                         # 忽略的构建中间文件
└── JobsCodexAccountSwitcher/
    ├── pyproject.toml
    ├── src/jobs_codex_account_switcher/
    ├── scripts/
    └── tests/
```

macOS 必须在 Mac 本机生成 APP / DMG；Windows 必须在 Windows 本机生成 EXE / ZIP。成品包含 Python 和依赖，使用成品不需要安装 Python。Windows 可选择普通安装或商店安装的实际 EXE 路径；受保护的商店路径可能因权限拒绝启动，此时需通过系统正常启动应用。

## 三、首次设置与操作 <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

1、运行切换器，确认 Codex 数据目录与桌面程序路径。默认读取 `CODEX_HOME`，未设置时使用当前用户的 `.codex`。macOS 按 Bundle ID 发现应用，兼容桌面应用更名为 `ChatGPT.app`；Windows 提供候选安装路径和手动选择 EXE。

2、默认自动打开免口令账户库，直接添加或保存账户即可。需要加密时勾选“启用口令保护”，设置并确认至少 12 字符的口令；开启后每次运行需解锁。旧加密库先输入原口令，解锁后取消勾选即可关闭保护，已有账户会保留。口令不保存，忘记后无法解密已有加密库。关闭保护时不再要求口令，账户凭据以明文保存；不要把账户库或真实登录文件提交到 Git。

3、在 Codex 正常登录账户 A，结束任务并完整退出桌面应用、CLI 和 IDE 后端。点击“保存当前登录账户”，在“账户备注”中填写 A。

4、选择“添加账户（独立浏览器授权）”，在“账户备注”中填写 B，在打开的浏览器中选择另一个 Google 账户并授权。此流程需要原生 Codex CLI；在应用数据目录的独立临时目录完成授权，按当前口令保护模式保存后删除临时明文，不调用退出登录、不覆盖当前桌面登录文件。macOS 自动发现桌面应用内置 CLI，Windows 可手动选择 `codex.exe`，不使用 npm 的 `.cmd`。CLI 不可用时，仍可手动在 Codex 登录 B 后完整退出并保存；退出登录可能撤销原会话，需要重新授权。

5、双击 A 或 B，或右键目标账户选择“切换账户并启动 Codex”。确认后自动关闭所选应用、切换缓存并重启。正常退出失败时会终止该应用残留进程，运行中的任务会中断，请先保存工作。启动失败可调整应用路径后手动启动；切换前的账户凭据已同步保存到列表。

6、在桌面应用检查目标邮箱、工作区并正常发起任务，无需返回工具点击核验成功。需要切回时，再双击原账户即可。

“完整退出”不等于关闭窗口；Mac 使用退出应用，Windows 退出窗口及后台进程。任务、云端聊天、连接、远程配对、插件权限与订阅可能属于账户或工作区，工具不迁移这些数据，不保证切换后仍可使用原账户资源。

在账户列表中右键目标账户，选择“更改备注”。输入框预填原备注，确定后立即保存并保持选中；取消不修改。备注为 1～80 个字符，不能与其它账户重复。此操作不需要退出 Codex，也不会修改登录凭据。账户行及列表空白处的右键菜单均提供“刷新缓存状态”；账户列表支持鼠标滚轮、触控板和垂直滚动条，账户多时仍可上下查看。

## 四、开发与打包 <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

需要 Python 3.11+。双击平台打包入口，先阅读内置自述。构建器创建 / 复用内层 `.venv`；依赖缺失时直接回车联网安装，任意非空输入取消，EOF 不代表同意。健康依赖不升级。直接调用构建器同样需要确认。

```shell
python3 ./JobsCodexAccountSwitcher/scripts/build.py
```

构建依赖为 [**PyInstaller**](https://pyinstaller.org/)、PySide6、cryptography、psutil；Mac 额外使用系统 `hdiutil`，Windows 创建快捷方式需要系统 PowerShell / WScript。安装失败立即停止，不清理旧产物。

打包前只清理本工具固定 `./dist/` 旧产物和指向该目录的旧快捷方式，拒绝符号链接 dist。每次构建共用本机时间 `YYYY.MM.DD HH-mm-ss`；成功后在第一层发布 Mac 相对 APP / DMG 链接或 Windows EXE / ZIP 的 `.lnk`，打开产物位置并启动新成品。成功结尾无需回车；失败不启动旧包。包未正式签名 / 公证。

源码运行：

```shell
python3 -m venv ./JobsCodexAccountSwitcher/.venv
./JobsCodexAccountSwitcher/.venv/bin/python -m pip install -e ./JobsCodexAccountSwitcher
./JobsCodexAccountSwitcher/.venv/bin/python ./JobsCodexAccountSwitcher/scripts/launch.py
```

Windows 将虚拟环境解释器替换为 `JobsCodexAccountSwitcher\.venv\Scripts\python.exe`。

## 五、数据、日志与恢复 <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

- `accounts.enc`：Qt 当前用户应用数据目录下 `Jobs/CodexAccountSwitcher` 的账户库。为兼容旧版保持文件名；免口令时内容为明文 JSON，开启口令后内容为密文，实际目录由系统决定；与源码、dist 分离，重新打包不清理账户库。
- 界面设置：Qt `QSettings` 保存外观、数据目录和应用路径，不保存口令或 Token。
- 构建日志：系统临时目录 `JobsCodexAccountSwitcher-build.log`；构建器异常记录在此，依赖安装和 PyInstaller 进度显示在终端。
- 应用不写凭据日志，界面只显示安全错误类型或明确的业务提示。
- 添加账户时临时目录 `enroll-*` 含短期明文凭据，正常完成或取消后删除。异常崩溃可能留下目录，确认工具已退出后删除；清理本机文件不撤销服务器会话。
- `instance.lock`：工具实例锁；Codex 数据目录的 `.jobs-account-switch.lock`：文件操作互斥锁。异常后需确认没有工具运行，再人工移除残留操作锁，禁止在并发切换时删除。
- 拷贝账户库到另一台电脑时，只有加密模式需要同一口令；免口令文件包含明文授权，可能暴露有效授权；默认每台机器分别正常授权，不提供自动同步。

## 六、验证与常见问题 <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

测试使用虚拟 Token 和临时目录，不访问真实 Google / ChatGPT 账户。测试覆盖密文与错误口令、源 / 目标凭据刷新保存、写入失败、运行保护、非 file 存储、名称冲突、锁、符号链接、独立授权录入及临时清理、三态主题与重启恢复，以及免口令默认打开、两种模式转换、旧加密库兼容、转换失败保护和取消设置。

```shell
cd ./JobsCodexAccountSwitcher
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

**为什么需要退出应用？** 运行中的桌面应用或后端可能持有旧凭据并刷新写回，覆盖文件会产生竞争。双击切换时工具自动退出所选应用，确认进程停止后再切换，避免凭据互相覆盖。其它仍运行的 CLI / IDE 后端会显示进程名及 PID，需要自行关闭。

**为什么不支持直接退出登录再恢复？** 退出登录可能撤销会话，恢复旧文件不能恢复已被服务器撤销的授权。

**配置是 file，桌面仍未切换？** 说明桌面应用可能有独立登录层或受管策略；重新走官方登录。此版本未提供官方桌面热切换接口，也不绕过认证。

**是否修改当前 Codex 账户做过验证？** 开发验证只读取存储元信息，不改真实登录文件、不关闭当前应用。真实两个账户的桌面验证需要分别授权后进行；Windows 原生打包和界面需在 Windows 本机验收。

参考：[官方认证与缓存说明](https://learn.chatgpt.com/docs/auth)、[官方 app-server 账户接口](https://learn.chatgpt.com/docs/app-server)。这些说明支持缓存机制，但不能保证每版桌面应用都允许外部切换登录缓存。

<a id="🔚" href="#前言" style="font-size:17px; color:green; font-weight:bold;">我是有底线的➤点我回到首页</a>
