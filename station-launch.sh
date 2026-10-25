#!/bin/sh
# Run Xash3D and the lightweight screen/input bridge on the Station.
set -eu
/system/vendor/quasar/q stop >/dev/null 2>&1 || true
amixer cset 'name=TAS5805M Master Playback Digital Mute Switc' 0,0 >/dev/null
amixer cset 'name=TAS5805M Digital Playback Volume' 0 >/dev/null
amixer cset 'name=SPK mute' 1 >/dev/null
base=/data/minecraft
realbase=$(readlink -f "$base")
"$base/hl-mount-assets.sh"
mkdir -p "$base/hl-ro/valve"
if ! grep -q " $realbase/hl-ro/valve " /proc/mounts; then
	mount --bind "$base/hl-assets" "$base/hl-ro/valve"
fi

for name in hl-game hl-web hl-xvfb; do
	file="$base/$name.pid"
	if [ -s "$file" ]; then
		oldpid=$(cat "$file")
		case "$oldpid" in ''|*[!0-9]*) oldpid=0;; esac
		if [ "$(readlink "/proc/$oldpid/root" 2>/dev/null || true)" = "$realbase" ]; then
			printf '%s is already running; stop it before starting again\n' "$name" >&2
			exit 1
		fi
		rm -f "$file"
	fi
done

mkdir -p "$base/proc" "$base/dev" "$base/tmp/frames"
if ! grep -q " $realbase/proc " /proc/mounts; then mount -t proc -o ro proc "$base/proc"; fi
if ! grep -q " $realbase/dev " /proc/mounts; then mount --bind /dev "$base/dev"; fi
if ! grep -q " $realbase/tmp " /proc/mounts; then mount -t tmpfs -o size=8m,mode=1777 tmpfs "$base/tmp"; mkdir -p "$base/tmp/frames"; fi

if [ "$(cat /sys/block/zram0/disksize)" = 0 ]; then
	printf '67108864\n' > /sys/block/zram0/mem_limit
	printf '134217728\n' > /sys/block/zram0/disksize
	mkswap /dev/zram0 >/dev/null
	swapon /dev/zram0
	: > "$base/zram-owned"
fi

export DISPLAY=:42
export LD_LIBRARY_PATH=/halflife:/lib

setsid chroot "$base" /bin/Xvfb :42 -screen 0 320x240x24 -fbdir /tmp/frames -fp /fonts -nolisten tcp -ac < /dev/null > "$base/hl-xvfb.log" 2>&1 &
printf '%s\n' "$!" > "$base/hl-xvfb.pid"
sleep 2
if ! kill -0 "$(cat "$base/hl-xvfb.pid")" 2>/dev/null; then cat "$base/hl-xvfb.log"; exit 1; fi

setsid chroot "$base" /bin/station-web < /dev/null > "$base/hl-web.log" 2>&1 &
printf '%s\n' "$!" > "$base/hl-web.pid"
sleep 1

setsid chroot "$base" /bin/sh /halflife/launch.sh < /dev/null > "$base/hl-game.log" 2>&1 &
sleep 1
printf 'Half-Life PID %s, viewer PID %s, Xvfb PID %s\n' \
	"$(cat "$base/hl-game.pid")" "$(cat "$base/hl-web.pid")" "$(cat "$base/hl-xvfb.pid")"
