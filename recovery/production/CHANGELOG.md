# Changelog

## [0.2.0] - 2026-10-16

### Highlights

- DVT development OS release with a signed RAUC A/B update bundle. The dev OTA offer is live as `0.2.0`. EVT3 builds are retired.
- Redesigned settings, notifications, on-screen keyboard, terminal and app switcher; hardware daemon and terminal ship together with terminal protocol v3.
- Device-password API across hardware D-Bus and Lapis app-edge; fingerprint deletion, capture and navigation recovery fixes.
- DP/capture stability fixes, microphone mute enforcement, persistent realtime audio scheduling and RTKit integration.
- USB/MTP gadget lifecycle improvements, provisioning/ADB hardening and zram/oomd policies.
- Linux 6.1.172, Chromium 154.0.8037.92 and WebRTC 0.14.5-1. The prebuilt pool is pinned and checked against the validated local image.
- Published Lapis dev userspace `2026.10.16`, including #799 and SDK inventory alignment, with minimum OS `0.2.0-rc.2`; image updater `1.1.11` comes from the same Lapis source.

### Deliberate holds and release status

- Kernel #178 remains reverted. Native microSD card detection from #238 is also reverted; polling is retained. The userspace RGA workaround remains enabled.
- Storage-expansion/pool redesign work (hwserviced #290 and mkosi #177), unselected OTA changes and the other recorded holds remain excluded.
- The app-window tester (#348) is a development tool and is not installed in the image.
- The DVT production image is built separately from the same frozen tag. It deliberately retains SIT userspace 2026.9.15, with OS floor 0.2.0-rc.1. Building these artifacts does not publish a SIT/production OTA offer or promote Nix userspace.

### Included integration PRs

These are the selected PRs represented by the frozen integration, including follow-up corrections already present when it was built. The attached manifest is the authority for exact source revisions. This list describes the final integration since rc.2; it is not a promise that current PR branch heads were used.

#### linux-rockchip-device-rockchip

- [#78](https://github.com/pamir-ai-pkgs/linux-rockchip-device-rockchip/pull/78) fix(configs): enable managed display stack in DVT images.
- [#79](https://github.com/pamir-ai-pkgs/linux-rockchip-device-rockchip/pull/79) kernel: record a per-CPU pstore function trace in the debug profile.

#### linux-rockchip-kernel

- [#202](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/202) Move the kernel to Rockchip develop-6.1 (6.1.172).
- [#203](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/203) docs: AGENTS.md: record the 6.1.172 develop-6.1 base.
- [#222](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/222) media: i2c: lt7911d: keep readers and register windows out of a firmware upgrade.
- [#223](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/223) media: i2c: lt7911d: fail QUERY_DV_TIMINGS at once without a stable input.
- [#226](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/226) arm64: configs: rk3576-pamir-debug: enable the function tracer.
- [#227](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/227) pstore/ftrace: walk records by payload size, not file size.
- [#229](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/229) Bluetooth: hci_aic: wait for the BSP before resetting the controller.
- [#230](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/230) misc: pamir-hwinfo: align with EEPROM identity schema v1.1.
- [#232](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/232) media: i2c: lt7911d: restart the bridge on DP entry and drop dead workers.
- [#233](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/233) media: videobuf2: cma-sg: stop reclaiming for high-order pages.
- [#234](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/234) arm64: dts: rockchip: rk3576-lapis: disable the cpu-sleep idle state.
- [#235](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/235) phy: rockchip: usbdp: check u3 readiness when usb joins a dp-held phy.
- [#238](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/238) arm64: dts: rockchip: rk3576-lapis: use the native microSD card detect. **Native card-detection portion reverted; polling retained.**
- [#240](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/240) spi: rockchip: initialize the default runtime PM autosuspend delay.
- [#242](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/242) soc: rockchip: performance: apply the RT uclamp default the level selects.
- [#243](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/243) arm64: configs: rk3576-pamir: stop boosting RT tasks to the top OPP.
- [#244](https://github.com/pamir-ai-pkgs/linux-rockchip-kernel/pull/244) hwmon: pwm-fan: time a few tach periods instead of taking an IRQ per edge.

#### lapis-hwserviced

- [#267](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/267) feat(gui): redesign the settings pages.
- [#275](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/275) Notification overhaul (mega PR): titles, icons, progress bars, countdowns, every healthd condition.
- [#286](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/286) Tidy four rc.3 review findings.
- [#303](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/303) feat(security): expose the device password to the Lapis app over D-Bus.
- [#304](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/304) fix(RC3 Settings): share auth feedback and verify Bluetooth audio identity.
- [#305](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/305) fix(auth): default-password start over needs no auth; reset asks for it.
- [#306](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/306) fix(gui): retain Bluetooth scan results after discovery ends.
- [#310](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/310) fix(fingerprint): retry CMD_VERSION once after a boot timeout.
- [#311](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/311) fix(polkit): make SetInputCurrentLimit root-only.
- [#312](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/312) fix(power): reject non-future ScheduleWakeup and roll back a failed set.
- [#314](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/314) fix(fingerprint): prepare capture after owned finger release.
- [#315](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/315) feat(gui): redesign notifications.
- [#317](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/317) feat(gui): tighten the status bar and set menu in Kohi Mono 13 Bold.
- [#318](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/318) fix(gui): return app notification Wi-Fi actions to their source window.
- [#321](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/321) fix(device-info): align About identity rows with EEPROM schema v1.1.
- [#325](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/325) feat(gui): redesign the on-screen keyboard.
- [#326](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/326) fix(fingerprint): own Delete completion and restore navigation.
- [#327](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/327) fix(gui): keep the charging bolt when the charger reports a full battery.
- [#328](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/328) fix(usbshare): ask for the password when no fingerprint is enrolled.
- [#330](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/330) feat(terminal): single keys, live terminal above the keyboard, physical typing (#323).
- [#337](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/337) fix(audio): mute every capture source when the microphone is off.
- [#338](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/338) perf(gui): render the idle Dashboard only when its pixels change.
- [#339](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/339) fix(kvm): report the video signal after the DP source is unplugged.
- [#340](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/340) fix(kvm): read the LT7911D firmware version once, off the poll path.
- [#341](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/341) fix(kvm)!: retire the UpgradeLtFirmware method.
- [#342](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/342) feat(gui): redesign the app switcher as the dashboard menu on the right.
- [#344](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/344) feat(app-window): B through the window (#329), close and PID from the switcher (#331).
- [#346](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/346) fix(fingerprint): recover navigation after sensor operation failures.
- [#348](https://github.com/pamir-ai-pkgs/lapis-hwserviced/pull/348) test(tools): app-window tester for B, closing and the PID. **Development tool only; not installed.**

#### lapis-ota

- [#114](https://github.com/pamir-ai-pkgs/lapis-ota/pull/114) fix(boot): drop the misc fallback for the current slot.
- [#118](https://github.com/pamir-ai-pkgs/lapis-ota/pull/118) feat(cli): report the bootloader journal in status --json.
- [#119](https://github.com/pamir-ai-pkgs/lapis-ota/pull/119) fix(cli): exit 11 from update when the update source is unreachable.

#### linux-rockchip-mkosi

- [#131](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/131) security: harden Lapis provisioning and development ADB.
- [#179](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/179) fix(audio): enable persistent real-time scheduling to address crackling.
- [#180](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/180) security: close the remaining ways to stop the Lapis platform daemons.
- [#184](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/184) fix(usb-gadget): start the gadget when the UDC appears after boot (#183).
- [#185](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/185) image: let non-root users ping.
- [#187](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/187) test(usb-gadget): cover late UDC and explicit-stop lifecycle.
- [#188](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/188) feat(mtp-gadget): show a read-only hint store while sharing is locked.
- [#189](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/189) image: add zram swap and a swap-full oomd kill (kernel#173).
- [#191](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/191) fix(polkit): let the lapis user's PipeWire take realtime through rtkit.
- [#195](https://github.com/pamir-ai-pkgs/linux-rockchip-mkosi/pull/195) build: pin the published 2026.10.16 dev userspace seed.

#### linux-rockchip-bsp-tools

- [#81](https://github.com/pamir-ai-pkgs/linux-rockchip-bsp-tools/pull/81) changelog: record the closed platform daemon stop routes.
- [#83](https://github.com/pamir-ai-pkgs/linux-rockchip-bsp-tools/pull/83) ci: default the release driver and its checks to the DVT board.
- [#84](https://github.com/pamir-ai-pkgs/linux-rockchip-bsp-tools/pull/84) prebuilts: pin rc.3 pool with MTP fixes and Chromium 154.

#### rockchip-packages

- [#17](https://github.com/pamir-ai-pkgs/rockchip-packages/pull/17) chromium: rebuild on Debian 154.0.8037.92-1~deb13u1.

#### manifest

- [#31](https://github.com/pamir-ai-pkgs/manifest/pull/31) ci: reconcile DVT release tooling with the verified publisher.
- [#34](https://github.com/pamir-ai-pkgs/manifest/pull/34) build: freeze v0.2.0 DVT inputs and separate OTA publication.

#### lapis

- [#799](https://github.com/pamir-ai-pkgs/lapis/pull/799) feat(device): expose the device password over app-edge.
- [#862](https://github.com/pamir-ai-pkgs/lapis/pull/862) fix(sdk): align hardware inventory with the rc3 device API.

#### lapis-healthd

- [#53](https://github.com/pamir-ai-pkgs/lapis-healthd/pull/53) Notification overhaul: healthd companion (B2, B3, B4, E6, TH3, GH2, CR15).

#### lapis-telemetryd

- [#23](https://github.com/pamir-ai-pkgs/lapis-telemetryd/pull/23) Upload through lapis-network's socket and drop data on telemetry.consent_required.

### Provenance

- Frozen source tag: [`rk3576-v0.2.0`](https://github.com/pamir-ai-pkgs/manifest/tree/rk3576-v0.2.0), manifest commit `d823b38bd2b21b26fc35715ea644dce3c02491da`.
- [Original image build](https://github.com/pamir-ai-pkgs/manifest/actions/runs/38851595535) built the dev image and signed bundle, then failed on the missing versioned canonical changelog section before upload.
- [Recovery and dev OTA publication](https://github.com/pamir-ai-pkgs/manifest/actions/runs/38868611054) verified original artifacts and published the dev offer.
- These release-specific notes were prepared from the frozen integration inventory and verification receipts after the build. They do not alter the source tag or claim the tagged canonical changelog had already been cut.
- The local integration image passed user validation. The final CI bundle was checked for signatures, contents and package/source parity; physical OTA installation of the final bundle remains a separate acceptance check.
