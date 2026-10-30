# <span id="前言">JobsCodexAccountSwitcher 构建辅助脚本</span>

![Jobs出品，必属精品](https://picsum.photos/1500/400)

<!-- jobs-toc:start -->
<!-- jobs-toc:source {"v":2,"token":"[toc]"} -->
<details>
<summary>📚 目录（点击展开 / 收起）</summary>

- [一、职责与入口](#files)
- [二、隔离验证](#tests)

</details>
<!-- jobs-toc:end -->

---

## 一、<span id="files">职责与入口</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

| 文件 | 职责 |
| --- | --- |
| [build.py](./build.py) | [**Python**](https://www.python.org/) 3.11+ 本机构建入口；先自述与确认，再检查依赖、清理本工具固定 `dist`、调用 [**PyInstaller**](https://pyinstaller.org/) 并校验产物。 |
| [launch.py](./launch.py) | 应用源码 / 冻结入口，转交当前工具的 Python 包。 |
| [artifact_shortcuts.py](./artifact_shortcuts.py) | 只清理指向本工具 `dist` 的旧快捷方式，在真实产物通过校验后发布第一层入口。 |

通常使用[外层平台入口](../../README.md)构建。直接运行 `build.py` 同样需要交互确认；`--help` 只显示参数，不构建或清理。构建只处理当前工具，成功后打开产物目录并启动本次软件；失败停止，不启动旧包。

[**Windows**](https://www.microsoft.com/windows) 入口只在当前进程树设置 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`；内层构建器同步配置标准输出、标准错误和构建日志。重定向后的子 Python 输出按 UTF-8 解码，不依赖系统 GBK 代码页，也不修改用户或系统环境变量。

## 二、<span id="tests">隔离验证</span> <a href="#前言" style="font-size:17px; color:green;"><b>🔼</b></a> <a href="#🔚" style="font-size:17px; color:green;"><b>🔽</b></a>

在本目录运行，不需要安装图形依赖或真正打包：

```shell
python -B -m unittest discover -s ../tests -p test_build_safety.py
```

夹具覆盖删除边界、依赖取消、失败停止、快捷方式、GBK 重定向与 UTF-8 子进程输出。Windows 因符号链接权限跳过的分支按测试输出标注；夹具通过不代表实际 GUI 或安装包已在目标系统验收。

<a id="🔚" href="#前言" style="font-size:17px; color:green; font-weight:bold;">我是有底线的➤点我回到首页</a>
