#!/bin/sh
set -eu
cd /halflife
/bin/station-web --mute
printf '%s\n' "$$" > /hl-game.pid
export XASH3D_BASEDIR=/halflife
export XASH3D_RODIR=/hl-ro
export XASH_STATION_TEXTURE_UPSCALE=1
export LD_LIBRARY_PATH=/halflife:/lib
export DISPLAY=:42
export SDL_VIDEODRIVER=x11
export SDL_RENDER_DRIVER=software
export SDL_AUDIODRIVER=alsa
export XASH_STATION_AUDIO_BUFFER=1
export ALSA_CONFIG_PATH=/halflife/asound.conf
if [ "$#" -eq 0 ]; then
    set -- +map c0a0
fi
exec /halflife/xash3d -game valve -ref soft -windowed -width 320 -height 240 \
    -console -dev 1 -log -nojoy "$@" +volume 0.5 +MP3Volume 0.25
