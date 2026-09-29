#!/usr/bin/env bash
# Cross-platform build/install/package driver for the uConsole Workbench.

set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
build_dir=${BUILD_DIR:-$root/build}
prefix=${PREFIX:-/usr/local}
destdir=${DESTDIR:-}

fail() {
    printf 'platform: %s\n' "$*" >&2
    exit 1
}

os=$(uname -s)
machine=$(uname -m)
case "$os:$machine" in
    Linux:x86_64|Linux:amd64) target=linux-x86_64 ;;
    Linux:aarch64|Linux:arm64) target=linux-aarch64 ;;
    Darwin:arm64) target=macos-arm64 ;;
    Darwin:x86_64) target=macos-x86_64 ;;
    *) fail "unsupported host: $os $machine" ;;
esac

stage=$build_dir/ide/$target/uconsole-workbench

install_deps() {
    if [[ $os == Linux ]]; then
        command -v apt-get >/dev/null 2>&1 || fail 'Linux dependency installation currently requires apt-get'
        sudo apt-get update
        sudo apt-get install -y \
            build-essential ca-certificates curl dfu-util libcap-ng-dev libglib2.0-dev \
            libgtk-3-dev libpixman-1-dev libslirp-dev ninja-build patch pkg-config \
            python3 python3-tk qemu-system-arm qemu-utils shellcheck tar unzip xvfb xz-utils dosfstools e2fsprogs
        if ! command -v arduino-cli >/dev/null 2>&1; then
            temp=$(mktemp -d)
            trap 'rm -rf -- "$temp"' EXIT
            curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
                | BINDIR="$temp" sh
            sudo install -m 0755 "$temp/arduino-cli" /usr/local/bin/arduino-cli
        fi
    else
        command -v brew >/dev/null 2>&1 || fail 'Homebrew is required; install it from https://brew.sh/'
        brew install arduino-cli dfu-util ninja pkg-config glib pixman libslirp python-tk qemu shellcheck dosfstools e2fsprogs
    fi

    # The legacy STM32 core is needed for firmware builds. Some ARM hosts need
    # the manual toolchain procedure documented in Code/uconsole_keyboard/README.md.
    if ! arduino-cli core list 2>/dev/null | grep -q '^stm32duino:STM32F1[[:space:]]'; then
        arduino-cli config init --overwrite
        arduino-cli config add board_manager.additional_urls \
            https://dan.drown.org/stm32duino/package_STM32duino_index.json
        arduino-cli core update-index
        arduino-cli core install stm32duino:STM32F1@2021.2.22 || fail \
            'STM32F1 core install failed; follow the architecture-specific manual setup in Code/uconsole_keyboard/README.md'
    fi
    find_tk_python >/dev/null || fail 'dependencies installed, but no Python 3 interpreter can import tkinter'
}

find_tk_python() {
    local candidate resolved
    for candidate in "${PYTHON:-}" python3 /usr/bin/python3 /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.12; do
        [[ -n $candidate ]] || continue
        resolved=$(command -v "$candidate" 2>/dev/null || true)
        [[ -n $resolved ]] || continue
        if "$resolved" -c 'import sys; sys.version_info >= (3, 12) or sys.exit(1); import tkinter' >/dev/null 2>&1; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

write_launcher() {
    mkdir -p "$stage/bin"
    cat > "$stage/bin/uconsole-workbench" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
prefix=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export UCONSOLE_ROOT="$prefix/libexec/uconsole-workbench"
export UCONSOLE_BUILD_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/uconsole-workbench"
tool=uconsole_workbench.py
probe='import sys; sys.version_info >= (3, 12) or sys.exit(1); import tkinter'
if [[ ${0##*/} == uconsole-mcp ]]; then
    tool=uconsole_mcp.py
    probe='import sys; sys.version_info >= (3, 12) or sys.exit(1); import json'
fi
for candidate in "${PYTHON:-}" python3 /usr/bin/python3 /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.12; do
    [[ -n $candidate ]] || continue
    resolved=$(command -v "$candidate" 2>/dev/null || true)
    if [[ -n $resolved ]] && "$resolved" -c "$probe" >/dev/null 2>&1; then
        exec "$resolved" "$UCONSOLE_ROOT/tools/$tool" "$@"
    fi
done
printf '%s: Python 3.12+ is required (with Tk for Workbench); run make deps or set PYTHON to a compatible interpreter\n' "${0##*/}" >&2
exit 1
EOF
    chmod 0755 "$stage/bin/uconsole-workbench"
    cp "$stage/bin/uconsole-workbench" "$stage/bin/uconsole-mcp"
}

build_ide() {
    local -a compile
    local stage_parent
    command -v python3 >/dev/null 2>&1 || fail 'python3 is required; run make deps'
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1 || fail 'building Workbench requires Python 3.12+ on PATH'
    command -v cc >/dev/null 2>&1 || fail 'a C compiler is required; run make deps'
    python3 -m compileall -q "$root/tools"
    UCONSOLE_BUILD_DIR="$build_dir" python3 "$root/tools/build_emulator_qemu.py"
    # Never clean/reuse a stage left by sudo install or another package build.
    # Keep completed stages available for inspection and build a fresh one.
    mkdir -p "$build_dir/ide/$target"
    stage_parent=$(mktemp -d "$build_dir/ide/$target/build.XXXXXX")
    stage=$stage_parent/uconsole-workbench
    mkdir -p "$stage/libexec/uconsole-workbench/tools" \
        "$stage/libexec/uconsole-workbench/Code/patch/qemu" \
        "$stage/share/doc/uconsole-workbench" \
        "$stage/share/uconsole-keyboard-flash"
    cp "$root"/tools/*.py "$stage/libexec/uconsole-workbench/tools/"
    cp -R "$root/tools/vendor" "$stage/libexec/uconsole-workbench/tools/"
    python3 "$root/tools/workbench_emulator.py" "$build_dir" "$root" "$stage/libexec/uconsole-workbench"
    cp "$root"/Code/patch/qemu/*.patch "$stage/libexec/uconsole-workbench/Code/patch/qemu/"
    cp "$root"/Code/patch/qemu/*.[ch] \
        "$stage/libexec/uconsole-workbench/Code/patch/qemu/"
    cp -R "$root/Code/uconsole_keyboard" "$stage/libexec/uconsole-workbench/Code/"
    mkdir -p "$stage/libexec/uconsole-workbench/assets"
    cp -R "$root/assets/schematics" "$stage/libexec/uconsole-workbench/assets/"
    cp "$root/uconsole-tasks.json" "$stage/libexec/uconsole-workbench/"
    cp "$root/docs/emulator.md" "$root/docs/emulator-validation.md" \
        "$root/docs/emulator-device-plan.md" "$root/docs/forge-agents.md" \
        "$root/docs/keyboard-usb-contract.md" "$root/docs/workbench-demo-ideas.md" \
        "$root/README.md" "$stage/share/doc/uconsole-workbench/"
    cp -R "$root/skills" "$stage/share/doc/uconsole-workbench/"
    cp -R "$root/docs/scenarios" "$stage/share/doc/uconsole-workbench/"
    make -C "$root" keyboard-oracle BUILD_DIR="$build_dir"
    mkdir -p "$stage/libexec/uconsole-workbench/bin"
    install -m 0755 "$build_dir/keyboard-oracle/keyboard-oracle" \
        "$stage/libexec/uconsole-workbench/bin/keyboard-oracle"
    compile=(cc)
    if [[ -n ${CPPFLAGS:-} ]]; then
        read -r -a flags <<< "$CPPFLAGS"
        compile+=("${flags[@]}")
    fi
    read -r -a flags <<< "${CFLAGS:--O2 -Wall -Wextra}"
    compile+=("${flags[@]}" "$root/Bin/uconsole_keyboard_flash/upload-reset/upload-reset.c")
    if [[ -n ${LDFLAGS:-} ]]; then
        read -r -a flags <<< "$LDFLAGS"
        compile+=("${flags[@]}")
    fi
    if [[ -n ${LDLIBS:-} ]]; then
        read -r -a flags <<< "$LDLIBS"
        compile+=("${flags[@]}")
    fi
    compile+=(-o "$build_dir/upload-reset.elf")
    "${compile[@]}"
    write_launcher
    printf 'Built uConsole Workbench for %s in %s\n' "$target" "$stage"
}

populate_flash_bundle() {
    local firmware flash_archive temp
    firmware=$build_dir/firmware/uconsole_keyboard.ino.bin
    if [[ ! -s $firmware ]] || ! python3 "$root/tools/firmware_manifest.py" check "$build_dir"; then
        make -C "$root" firmware BUILD_DIR="$build_dir"
    fi
    flash_archive=$(python3 "$root/tools/package_flash.py" "$build_dir" | tail -1)
    temp=$(mktemp -d)
    tar -xzf "$flash_archive" -C "$temp"
    cp -R "$temp/uconsole_keyboard_flash/." "$stage/share/uconsole-keyboard-flash/"
    rm -rf -- "$temp"
}

package_ide() {
    build_ide
    populate_flash_bundle
    output=$build_dir/uconsole-workbench-$target.tar.gz
    tar -czf "$output" -C "$(dirname "$stage")" "$(basename "$stage")"
    printf '%s\n' "$output"
}

install_ide() {
    build_ide
    populate_flash_bundle
    install -d "$destdir$prefix/bin" "$destdir$prefix/libexec" "$destdir$prefix/share"
    cp -R "$stage/libexec/uconsole-workbench" "$destdir$prefix/libexec/"
    cp -R "$stage/share/doc" "$destdir$prefix/share/"
    cp -R "$stage/share/uconsole-keyboard-flash" "$destdir$prefix/share/"
    install -m 0755 "$stage/bin/uconsole-workbench" "$destdir$prefix/bin/uconsole-workbench"
    install -m 0755 "$stage/bin/uconsole-mcp" "$destdir$prefix/bin/uconsole-mcp"
    printf 'Installed uConsole Workbench under %s%s\n' "$destdir" "$prefix"
}

case ${1:-} in
    target) printf '%s\n' "$target" ;;
    python) find_tk_python || fail 'no Python 3.12+ interpreter with tkinter found; run make deps' ;;
    deps) install_deps ;;
    build) build_ide ;;
    run)
        python=$(find_tk_python) || fail 'no Python 3 interpreter with tkinter found; run make deps'
        export UCONSOLE_ROOT=$root
        export UCONSOLE_BUILD_DIR=$build_dir
        exec "$python" "$root/tools/uconsole_workbench.py" "${@:2}"
        ;;
    install) install_ide ;;
    package) package_ide ;;
    *) fail 'usage: scripts/platform.sh {target|python|deps|build|run|install|package}' ;;
esac
