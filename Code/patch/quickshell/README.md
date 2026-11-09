# Quickshell on the Trixie ARM64 image

Upstream issue [#45](https://github.com/clockworkpi/uConsole/issues/45) requests
Quickshell in the vendor package repository. This directory records a verified
source build for the Debian Trixie ARM64 environment used by the current CM4
pi-gen branch. It does not publish a package to that repository.

Tested source: Quickshell v0.3.1, commit
`1a4716cde794a59928d9d9fc15f2afc7a95de360`, with Qt 6.8.2. Build in a Trixie
container or chroot, not directly against an older image's Qt installation.
Quickshell uses Qt private APIs; rebuild it when changing the Qt version.

## Dependencies

Enable the official `trixie-backports` suite in the build environment and install
`wayland-protocols` from it. The tested version is 1.47. Trixie's 1.44 lacks the
`ext-background-effect-v1.xml` protocol needed by this Quickshell revision,
although its configure-time version check accepts 1.44.

```sh
apt-get update
apt-get install -y build-essential cmake ninja-build git ca-certificates pkg-config \
  qt6-base-dev qt6-base-private-dev qt6-declarative-dev qt6-declarative-private-dev \
  qt6-wayland-dev qt6-wayland-private-dev qt6-shadertools-dev spirv-tools \
  libcli11-dev libdrm-dev libgbm-dev libegl-dev libvulkan-dev libwayland-dev \
  libxcb1-dev libpipewire-0.3-dev libpam0g-dev libpolkit-agent-1-dev libglib2.0-dev \
  libjemalloc-dev libunwind-dev libdwarf-dev libqt6svg6 qml6-module-qtquick \
  qml6-module-qtqml qml6-module-qtqml-workerscript qml6-module-qtquick-window \
  xvfb xauth
apt-get install -y -t trixie-backports wayland-protocols
```

## Build and test

```sh
git clone https://github.com/quickshell-mirror/quickshell.git
cd quickshell
git checkout --detach 1a4716cde794a59928d9d9fc15f2afc7a95de360
# Substitute the absolute path to this uConsole checkout.
git apply /path/to/uConsole/Code/patch/quickshell/0001-wait-for-popup-position.patch
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_INSTALL_PREFIX=/usr -DDISTRIBUTOR='uConsole local Trixie build' \
  -DVENDOR_CPPTRACE=ON -DBUILD_TESTING=ON
cmake --build build -j4
LANG=C.UTF-8 xvfb-run -a ctest --test-dir build --output-on-failure
build/src/quickshell --version
```

All default features remain enabled. The build downloads the upstream-pinned
cpptrace dependency and its dependencies. The local patch changes only the
popup test: window movement is asynchronous, so it waits for the expected
coordinates with Qt's bounded `QTRY_COMPARE` instead of asserting immediately.
The original test fails under Xvfb; with this change all nine test suites pass.
No runtime implementation is changed. An offscreen-only Qt test run is not a
substitute for the X11 test run.

## Local Debian package

After the successful build and test run, invoke the packaging tool inside the
same Trixie environment (requires Python 3 and `dpkg-dev`):

```sh
python3 /path/to/uConsole/tools/package_quickshell.py /path/to/quickshell /path/to/packages
```

The tool rebuilds and runs all nine test suites before creating
`quickshell_0.3.1-0uconsole1_arm64.deb` on ARM64. It selects the runtime binary,
desktop file, icon and license notices, and derives ELF dependencies with
`dpkg-shlibdeps`. Explicit dependencies cover QML imports, SVG and the Wayland
plugin. The generated metadata pins the Qt private ABIs to 6.8.2. This package
is for Trixie; do not install it on older Debian/Ubuntu images.

## Runtime verification

The generated ARM64 package installed successfully into a fresh
`debian:trixie-slim` container using only Trixie runtime dependencies. As an
unprivileged user, this check loaded a floating QtQuick window, printed
`UCONSOLE_RUNTIME_OK`, and exited successfully:

```sh
LANG=C.UTF-8 timeout 20 xvfb-run -a quickshell \
  --path /path/to/uConsole/tests/quickshell-smoke.qml
```

Install `xvfb` and `xauth` for this headless check. It exercises the installed
binary and QML imports; it does not prove hardware acceleration, the Wayland
session, or device-service integration on a uConsole.

## Remaining integration

The compiled program reports Quickshell 0.3.1. A staged CMake installation also
succeeds, but the default install includes vendored dependency development files
alongside the executable, desktop file and icon. Do not copy that whole staging
tree over a distribution installation: the packaging tool above selects runtime assets instead. Its output still
needs vendor review before repository publication. Vendor repository publication
requires its owner's access. A real uConsole Wayland session and an actual shell
configuration still need acceptance testing.
