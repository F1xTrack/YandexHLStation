#!/bin/sh
set -eu
task_base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
toolchain="$task_base/toolchains/gcc-arm-9.2-2019.12-x86_64-arm-none-linux-gnueabihf"
export PATH="$toolchain/bin:$PATH"
export CC=arm-none-linux-gnueabihf-gcc
export CXX=arm-none-linux-gnueabihf-g++
export AR=arm-none-linux-gnueabihf-gcc-ar
export RANLIB=arm-none-linux-gnueabihf-gcc-ranlib
export STRIP=arm-none-linux-gnueabihf-strip
export CFLAGS='-O2 -march=armv7-a -mtune=cortex-a53 -mfpu=neon-vfpv4 -mfloat-abi=hard'
export CXXFLAGS="$CFLAGS"
export CPPFLAGS="-I$task_base/build/include -I$task_base/deps-armhf/include"
export LDFLAGS="-L$task_base/deps-armhf/lib -Wl,-rpath-link,$task_base/build/shared-runtime/lib"
export PKG_CONFIG_LIBDIR="$task_base/deps-armhf/lib/pkgconfig"
mkdir -p "$task_base/build-sdl2"
cd "$task_base/build-sdl2"
if [ ! -s "$task_base/sdl2-armhf/lib/libSDL2.so" ] || [ "${REBUILD_SDL:-0}" = 1 ]; then
"$task_base/SDL2-2.32.10/configure" --host=arm-none-linux-gnueabihf --prefix="$task_base/sdl2-armhf" \
    --disable-static --enable-shared --enable-video-x11 --enable-x11-shared \
    --enable-alsa --enable-alsa-shared --disable-alsatest \
    --disable-video-wayland --disable-video-kmsdrm --disable-video-vulkan \
    --disable-video-x11-xinput --disable-video-x11-xfixes --disable-video-x11-xrandr \
    --disable-video-x11-xcursor --disable-video-x11-scrnsaver --disable-video-x11-xdbe \
    --disable-video-opengl --disable-video-opengles --disable-render-opengl --disable-render-opengles \
    --disable-pulseaudio --disable-pipewire --disable-jack --disable-esd --disable-arts \
    --disable-nas --disable-sndio --disable-libudev --disable-dbus --disable-ibus --disable-fcitx \
    --disable-haptic --disable-joystick --disable-sensor
make clean
make -j4
make install
fi
cd "$task_base/xash3d-fwgs"
unset CPPFLAGS LDFLAGS
export CFLAGS="$CFLAGS -I$task_base/build/include"
export CXXFLAGS="$CFLAGS"
export LINKFLAGS="-Wl,-rpath-link,$task_base/build/shared-runtime/lib"
./waf configure --out=build-station --enable-bundled-deps --disable-gl --enable-soft \
    --disable-werror --enable-lto --enable-stbtt --sdl2="$task_base/sdl2-armhf"
./waf build -j4
./waf install --destdir="$task_base/engine-station"
