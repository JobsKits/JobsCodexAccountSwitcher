#!/bin/zsh
# shell: zsh
# 脚本自述：在本机生成账户切换器 APP/DMG；仅构建，不切换 Codex 账户。
# 清理本工具 dist 旧包；缺失依赖由 Python 构建器回车确认安装。
# 展示范围并等待确认，确认前不修改文件。
# 仅渲染自述：标题红色加粗，编号正文蓝色常规字重；非彩色终端输出纯文本。
jobs_intro_style() {
  local intro_color=0
  if [ -t 1 ] && [ -n "${TERM:-}" ] && [ "${TERM:-}" != dumb ] &&
     [ -z "${NO_COLOR+x}" ] && [ "${PLAIN_OUTPUT:-0}" != 1 ] &&
     [ "${IS_SOURCETREE_RUNTIME:-0}" != 1 ]; then
    intro_color=1
  fi
  /usr/bin/awk -v color="$intro_color" -v role="${1:-body}" '
    BEGIN { esc = sprintf("%c", 27) }
    {
      gsub(esc "\\[[0-9;]*m", "")
      gsub(/\\(033|e|x1[bB])\[[0-9;]*m/, "")
      if (!color || $0 ~ /^[[:space:]]*$/) { print; next }
      numbered = ($0 ~ /^[[:space:]➤ℹ🔹✔⚠]*([0-9]+[、.)）]|[0-9]+️⃣|[-•])/)
      heading = ($0 ~ /^[[:space:]]*#{1,6}[[:space:]]/ || $0 ~ /[：:][[:space:]]*$/ || $0 ~ /^[[:space:]]*[=━─-]{3}/)
      title = (!numbered && (role == "title" || heading))
      if (role == "auto" && !seen && !numbered) title = 1
      if ($0 !~ /^[[:space:]]*[=━─-]+[[:space:]]*$/) seen = 1
      printf "%s%s%s\n", esc (title ? "[1;31m" : "[0;34m"), $0, esc "[0m"
    }
  '
}
show_script_intro_and_wait() {
    print -r -- 'Jobs Codex 账户切换器 · macOS 打包' | jobs_intro_style body
    print -r -- '清理当前工具 dist 的旧包；缺失依赖回车安装、任意字符取消。' | jobs_intro_style body
    print -r -- '成功后更新 APP/DMG 快捷方式、打开位置并启动成品；不切换 Codex 账户。' | jobs_intro_style body
    print -r -- '日志：系统临时目录 JobsCodexAccountSwitcher-build.log。' | jobs_intro_style body
    [[ -t 0 ]] || { print -u2 -- '请在终端运行。'; exit 1; }
    read -r '?按回车继续，Ctrl+C 取消：' reply
}
# 验证系统 Python 及完整构建路径。
check_environment() {
    command -v python3 >/dev/null || { print -u2 -- '请安装 Python 3.11+：https://www.python.org/downloads/'; exit 1; }
    python3 -c 'import sys,venv,pathlib; assert sys.version_info >= (3,11)' || exit 1
}
# 执行构建器，其自述同时保护直接调用入口。
run_build() {
    local script_dir="${0:A:h}"
    python3 "$script_dir/JobsCodexAccountSwitcher/scripts/build.py"
    local build_result=$?
    if (( build_result != 0 )); then
        print -u2 -- '打包失败，请查看构建器错误与系统临时目录日志。'
    fi
    exit "$build_result"
}
# 编排双击入口。
main() {
    show_script_intro_and_wait # 先确认范围，避免误触。
    check_environment # 确認 Python 和标准库健康。
    run_build # 交给工程构建器生成并启动本机成品。
}
main "$@"
