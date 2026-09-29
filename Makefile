ARDUINO_CLI ?= arduino-cli
PYTHON ?= python3
FQBN ?= stm32duino:STM32F1:genericSTM32F103R:device_variant=STM32F103RB,upload_method=DFUUploadMethod,cpu_speed=speed_48mhz,opt=osstd
BUILD_DIR ?= $(CURDIR)/build
CFLAGS ?= -O2 -Wall -Wextra

.PHONY: all deps build run install package release firmware flash-tool flash-bundle modem-flash-tool install-modem-updater check check-gui clean
all: firmware flash-tool modem-flash-tool

# Stable, platform-aware entry points for IDE integrations and releases.
deps:
	./scripts/platform.sh deps

build:
	./scripts/platform.sh build

run:
	./scripts/platform.sh run

install:
	./scripts/platform.sh install

package:
	./scripts/platform.sh package

RELEASE ?= patch
release:
	./scripts/release.sh '$(RELEASE)'

firmware:
	python3 tools/firmware_manifest.py build '$(BUILD_DIR)' --fqbn '$(FQBN)' --arduino-cli '$(ARDUINO_CLI)'

.PHONY: keyboard-oracle
keyboard-oracle:
	mkdir -p '$(BUILD_DIR)/keyboard-oracle'
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -std=c++17 -Wall -Wextra -I tools/keyboard-oracle tools/keyboard-oracle/main.cpp $(LDFLAGS) $(LDLIBS) -o '$(BUILD_DIR)/keyboard-oracle/keyboard-oracle'

flash-tool:
	mkdir -p '$(BUILD_DIR)'
	$(CC) $(CPPFLAGS) $(CFLAGS) Bin/uconsole_keyboard_flash/upload-reset/upload-reset.c $(LDFLAGS) $(LDLIBS) -o '$(BUILD_DIR)/upload-reset.elf'

flash-bundle: firmware flash-tool
	python3 tools/package_flash.py '$(BUILD_DIR)'

modem-flash-tool:
	mkdir -p '$(BUILD_DIR)/modem-fastboot'
	tar -xzf Bin/4G/LE20B04SIM7600G22_cpi_arm64.tar.gz -C '$(BUILD_DIR)/modem-fastboot' --strip-components=2 LE20B04SIM7600G22_cpi_arm64/fastboot
	patch -d '$(BUILD_DIR)/modem-fastboot' -p1 < Code/patch/modem/fastboot-path-bounds.patch
	$(MAKE) -B -C '$(BUILD_DIR)/modem-fastboot' CC='$(CC)' all

check:
	$(PYTHON) tools/run_host_tests.py discover -s tests -v
	shellcheck Code/scripts/uconsole-4g-cm5 Bin/uconsole_keyboard_flash/maple_upload Bin/uconsole_keyboard_flash/flash.sh scripts/platform.sh scripts/release.sh

check-gui:
	xvfb-run -a /usr/bin/python3 -m unittest discover -s tests -p 'test_modem_gui.py' -v

# Build as the normal user before running this installation target with sudo.
install-modem-updater:
	test -x '$(BUILD_DIR)/modem-fastboot/bin/fastboot'
	install -d '$(DESTDIR)/usr/lib/uconsole-modem' '$(DESTDIR)/usr/bin' '$(DESTDIR)/usr/share/applications'
	install -m 755 Code/scripts/uconsole-modem-flash.py Code/scripts/uconsole-modem-gui.py '$(DESTDIR)/usr/lib/uconsole-modem/'
	install -m 644 Code/scripts/modem-firmware-manifest.json '$(DESTDIR)/usr/lib/uconsole-modem/'
	install -m 755 '$(BUILD_DIR)/modem-fastboot/bin/fastboot' '$(DESTDIR)/usr/lib/uconsole-modem/fastboot'
	ln -sfn ../lib/uconsole-modem/uconsole-modem-gui.py '$(DESTDIR)/usr/bin/uconsole-modem-updater'
	install -m 644 Code/scripts/desktop/uconsole-modem-updater.desktop '$(DESTDIR)/usr/share/applications/'

clean:
	rm -rf -- '$(BUILD_DIR)'

.PHONY: emulator-build emulator-workbench check-emulator
emulator-build:
	python3 tools/build_emulator_qemu.py

emulator-workbench:
	python3 tools/uconsole_workbench.py

check-emulator: keyboard-oracle
	python3 -m unittest discover -s tests -p 'test_emulator.py' -v
	python3 tools/test_emulator_cpu_lifecycle.py --qemu '$(BUILD_DIR)/emulator/qemu-build/qemu-system-aarch64' --output "$$(mktemp -d '$(BUILD_DIR)/emulator/cpu-lifecycle.XXXXXX')/evidence"
	python3 tools/test_emulator_watchdog.py
	python3 tools/test_emulator_pmic.py
	python3 tools/test_emulator_adc101c.py
	python3 tools/test_emulator_adc_migration.py
	python3 tools/test_emulator_pmic_migration.py
	python3 tools/test_emulator_gpio.py
	python3 tools/test_emulator_firmware_gpio.py
	python3 tools/test_emulator_gic.py
	python3 tools/test_emulator_usb_wakeup.py
	python3 tools/test_emulator_usb_frames.py
	python3 tools/test_emulator_audio.py --qemu '$(BUILD_DIR)/emulator/qemu-build/qemu-system-aarch64' --reconnect-cycles 200 --capture
ifeq ($(shell uname -s),Linux)
	python3 tools/test_emulator_wav.py --source '$(BUILD_DIR)/emulator/qemu-build/qemu-source/audio/wavaudio.c' --qemu '$(BUILD_DIR)/emulator/qemu-build/qemu-system-aarch64'
endif
	python3 tools/test_emulator_keyboard.py --oracle '$(BUILD_DIR)/keyboard-oracle/keyboard-oracle'
	python3 tools/test_emulator_observations.py --output "$$(mktemp -d '$(BUILD_DIR)/emulator/schematic-counters.XXXXXX')/evidence"
