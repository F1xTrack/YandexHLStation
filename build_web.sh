#!/bin/sh
set -eu
task_base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
toolchain="$task_base/toolchains/gcc-arm-9.2-2019.12-x86_64-arm-none-linux-gnueabihf/bin"
runtime="$task_base/build/shared-runtime/lib"
"$toolchain/arm-none-linux-gnueabihf-gcc" -std=gnu11 -O2 -Wall -Wextra \
    -march=armv7-a -mtune=cortex-a53 -mfpu=neon-vfpv4 -mfloat-abi=hard \
    -I"$task_base/build/include" -I"$task_base/deps-armhf/include" \
    "$task_base/station-web.c" "$runtime/libX11.so.6" "$runtime/libXtst.so.6" \
    "$runtime/libz.so.1" "$runtime/libasound.so.2" \
    -Wl,-rpath-link,"$runtime" -o "$task_base/station-web-armhf"
"$toolchain/arm-none-linux-gnueabihf-strip" "$task_base/station-web-armhf"
