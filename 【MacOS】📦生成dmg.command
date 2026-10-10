#!/bin/zsh
# 脚本自述：在本机生成账户切换器、独立 Token 浮窗入口及 DMG；不切换 Codex 账户。
# shell: zsh
# 清理本工具 dist 旧包；缺失依赖由 Python 构建器回车确认安装。
# 展示范围并等待确认；成功后发布两 APP/DMG 链接、打开位置并启动 Token 浮窗。
SCRIPT_FILE="${(%):-%x}"
SCRIPT_DIR=""
LOG_FILE=""
# 只为自述准备脚本定位与日志路径，不产生文件写入。
prepare_display_paths() {
    SCRIPT_DIR="${SCRIPT_FILE:A:h}"
    LOG_FILE="${TMPDIR:-/tmp}/JobsCodexAccountSwitcher-build.log"
}
# 彩色终端使用红色粗体标题、蓝色常规正文，其它环境保持纯文本。
print_intro_line() {
    local role="$1"
    local message="$2"
    if [[ -t 1 && -n "${TERM:-}" && "${TERM:-}" != dumb && -z "${NO_COLOR+x}" ]]; then
        if [[ "$role" == title ]]; then
            printf '\033[1;31m%s\033[0m\n' "$message"
        else
            printf '\033[0;34m%s\033[0m\n' "$message"
        fi
    else
        print -r -- "$message"
    fi
}
# 展示构建范围，在真实业务开始前等待回车确认。
show_script_intro_and_wait() {
    prepare_display_paths
    print_intro_line title 'Jobs Codex 账户切换器与 Token 浮窗 · macOS 打包'
    print_intro_line body '1、清理当前工具 dist 旧包；缺失依赖回车安装、任意字符取消。'
    print_intro_line body '2、生成账户工具 APP、独立 Token APP 与 DMG，两 APP 必须安装在同一目录。'
    print_intro_line body '3、成功后更新两 APP/DMG 链接、打开位置并启动 Token 浮窗。'
    print_intro_line body '4、只构建软件，不切换账户，不写入 Codex 钩子。'
    print_intro_line body "日志：${LOG_FILE}；按 Ctrl+C 取消。"
    [[ -t 0 ]] || { print -u2 -- '请在终端运行。'; exit 1; }
    local answer=''
    IFS= read -r '?按回车继续；输入任意字符或 Ctrl+C 取消：' answer || exit 1
    [[ -z "$answer" ]] || { print -r -- '已取消。'; exit 1; }
}
# 在确认后初始化原生 zsh 运行选项。
initialize_runtime() {
    setopt NO_NOMATCH
    setopt PIPE_FAIL
}
# 验证系统 Python 及完整构建路径。
check_environment() {
    command -v python3 >/dev/null || { print -u2 -- '请安装 Python 3.11+：https://www.python.org/downloads/'; exit 1; }
    python3 -c 'import sys,venv,pathlib; assert sys.version_info >= (3,11)' || exit 1
}
# 执行构建器，其自述同时保护直接调用入口。
run_build() {
    PYTHONDONTWRITEBYTECODE=1 python3 "$SCRIPT_DIR/JobsCodexAccountSwitcher/scripts/build.py"
    local build_result=$?
    if (( build_result != 0 )); then
        print -u2 -- '打包失败，请查看构建器错误与系统临时目录日志。'
    fi
    exit "$build_result"
}
# 编排双击入口。
main() {
    show_script_intro_and_wait # 先确认范围，避免误触。
    initialize_runtime # 确认后设置原生 zsh 运行选项。
    check_environment # 确认 Python 和标准库健康。
    run_build # 生成两个真实入口及分发包，再定位并启动 Token 浮窗。
}
main "$@"
