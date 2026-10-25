#!/bin/sh
# Mount Half-Life's compressed internal-flash data after a Station reboot.
set -eu
base=/data/minecraft
image="$base/hl-assets.sqfs"
destination="$base/hl-assets"
test -s "$image"
test -x "$base/bin/squashfuse_ll"
mkdir -p "$destination"
real_destination=$(readlink -f "$destination")
if grep -q " $real_destination fuse.squashfuse_ll " /proc/mounts; then
    exit 0
fi
if grep -q " $real_destination " /proc/mounts; then
    printf '%s already has another filesystem mounted\n' "$destination" >&2
    exit 1
fi
nohup "$base/bin/squashfuse_ll" -f -s -o ro "$image" "$destination" \
    </dev/null >"$base/hl-squash.log" 2>&1 &
printf '%s\n' "$!" >"$base/hl-squash.pid"
sleep 1
if ! grep -q " $real_destination fuse.squashfuse_ll " /proc/mounts; then
    cat "$base/hl-squash.log" >&2
    exit 1
fi
