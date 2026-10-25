#!/bin/sh
# Build the compressed-assets reader on the host, against Station-era glibc.
set -eu
task_base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
toolchain="$task_base/toolchains/gcc-arm-9.2-2019.12-x86_64-arm-none-linux-gnueabihf"
export PATH="$toolchain/bin:$PATH"
export CC=arm-none-linux-gnueabihf-gcc
export AR=arm-none-linux-gnueabihf-ar
export RANLIB=arm-none-linux-gnueabihf-ranlib
export CFLAGS='-Os -mcpu=cortex-a53 -mfpu=neon-fp-armv8 -mfloat-abi=hard -fPIC'
task_prefix="$task_base/deps-armhf"
mkdir -p "$task_prefix" "$task_base/build-deps/fuse" "$task_base/build-deps/xz" "$task_base/build-deps/squashfuse"
cd "$task_base/build-deps/fuse"
"$task_base/fuse-2.9.9/configure" --host=arm-none-linux-gnueabihf --prefix="$task_prefix" \
    --disable-shared --enable-static --disable-util --disable-example --disable-mtab
make -j4
make install
cd "$task_base/build-deps/xz"
"$task_base/xz-5.2.5/configure" --host=arm-none-linux-gnueabihf --prefix="$task_prefix" \
    --disable-shared --enable-static --disable-xz --disable-xzdec --disable-lzmadec \
    --disable-lzmainfo --disable-scripts --disable-doc --disable-nls
make -j4
make install
cd "$task_base/build-deps/squashfuse"
export PKG_CONFIG_LIBDIR="$task_prefix/lib/pkgconfig"
export CPPFLAGS="-I$task_prefix/include -DFUSE_USE_VERSION=26"
export LDFLAGS="-L$task_prefix/lib"
export LIBS='-pthread -ldl'
"$task_base/squashfuse-0.6.5/configure" --host=arm-none-linux-gnueabihf --prefix="$task_prefix" \
    --disable-shared --enable-static --disable-high-level --disable-demo --disable-multithreading \
    --with-fuse="$task_prefix" --with-xz="$task_prefix" \
    --without-zlib --without-lzo --without-lz4 --without-zstd
make -j4
arm-none-linux-gnueabihf-strip squashfuse_ll
cp squashfuse_ll "$task_base/squashfuse_ll-armhf"
arm-none-linux-gnueabihf-readelf -d "$task_base/squashfuse_ll-armhf" | grep NEEDED
