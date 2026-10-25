#!/usr/bin/env python3
"""Structural and decoder checks; this does not establish campaign playability."""
import ctypes as c
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import wave
from compact_assets import lump_data, wad_entries, wav_chunks

source, compact = map(Path, sys.argv[1:3])
mpg = c.CDLL('libmpg123.so.0')
mpg.mpg123_new.restype = c.c_void_p
mpg.mpg123_new.argtypes = [c.c_char_p, c.POINTER(c.c_int)]
mpg.mpg123_open_feed.argtypes = [c.c_void_p]
mpg.mpg123_decode.argtypes = [c.c_void_p, c.c_void_p, c.c_size_t, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
mpg.mpg123_getformat.argtypes = [c.c_void_p, c.POINTER(c.c_long), c.POINTER(c.c_int), c.POINTER(c.c_int)]
mpg.mpg123_delete.argtypes = [c.c_void_p]
mpg.mpg123_init()
out = c.create_string_buffer(32768)
report = {'maps': 0, 'textures': 0, 'navigation_graphs': 0, 'pcm_original': 0, 'mp3_decoded': 0, 'max_duration_error_ms': 0}
for name in ['gfx.wad', 'fonts.wad', 'cached.wad', 'decals.wad', 'spraypaint.wad']:
    assert (compact / name).read_bytes() == (source / name).read_bytes(), name
common = wad_entries((compact / 'station.wad').read_bytes())
for path in sorted((compact / 'maps').glob('*.bsp')):
    old = lump_data((source / 'maps' / path.name).read_bytes())
    new = lump_data(path.read_bytes())
    for index in set(range(15)) - {0, 2, 8}:
        assert old[index] == new[index], (path.name, index)
    assert re.sub(rb'"wad"\s*"[^"]*"', b'', old[0], count=1) == re.sub(rb'"wad"\s*"[^"]*"', b'', new[0], count=1)
    wad_list = re.search(rb'"wad"\s*"([^"]*)"', new[0])[1].decode().split(';')
    textures = dict(common)
    for name in wad_list[1:]:
        textures.update(wad_entries((compact / name).read_bytes()))
    n, = struct.unpack_from('<i', new[2])
    assert n == struct.unpack_from('<i', old[2])[0]
    for index in range(n):
        offset, = struct.unpack_from('<i', new[2], 4 + index * 4)
        original_offset, = struct.unpack_from('<i', old[2], 4 + index * 4)
        if offset == -1:
            assert original_offset == -1
            continue
        assert new[2][offset:offset + 24] == old[2][original_offset:original_offset + 24]
        assert new[2][offset + 24:offset + 40] == bytes(16)
        name = new[2][offset:offset + 16].split(b'\0', 1)[0].lower()
        assert name in textures, (path.name, name)
        report['textures'] += 1
    report['maps'] += 1
for path in (source / 'maps' / 'graphs').glob('*.nod'):
    if (compact / 'maps' / (path.stem + '.bsp')).is_file():
        assert (compact / 'maps' / 'graphs' / path.name).read_bytes() == path.read_bytes(), path
        report['navigation_graphs'] += 1
for path in sorted((compact / 'sound').rglob('*.wav')):
    chunks = dict(wav_chunks(path.read_bytes()))
    original = source / path.relative_to(compact)
    if struct.unpack_from('<H', chunks[b'fmt '])[0] != 85:
        assert path.read_bytes() == original.read_bytes(), path
        report['pcm_original'] += 1
        continue
    assert b'cue ' not in dict(wav_chunks(original.read_bytes()))
    payload = chunks[b'data']
    error = c.c_int()
    handle = mpg.mpg123_new(None, c.byref(error))
    assert handle and error.value == 0
    assert mpg.mpg123_open_feed(handle) == 0
    done = c.c_size_t()
    assert mpg.mpg123_decode(handle, payload, len(payload), None, 0, c.byref(done)) == -11, path
    rate, channels, encoding = c.c_long(), c.c_int(), c.c_int()
    assert mpg.mpg123_getformat(handle, c.byref(rate), c.byref(channels), c.byref(encoding)) == 0
    assert (rate.value, channels.value, encoding.value) == (11025, 1, 208), path
    size = 0
    while True:
        result = mpg.mpg123_decode(handle, None, 0, out, len(out), c.byref(done))
        size += done.value
        if result not in (0, -11):
            assert result in (-10, -12), (path, result)
            break
    mpg.mpg123_delete(handle)
    samples, = struct.unpack_from('<I', chunks[b'fact'])
    with wave.open(str(original)) as wav:
        expected = round(wav.getnframes() * 11025 / wav.getframerate())
    assert samples == expected, path
    error_ms = abs(size // 2 - expected) * 1000 / 11025
    assert error_ms < 10, (path, size // 2, expected, error_ms)
    report['max_duration_error_ms'] = max(report['max_duration_error_ms'], error_ms)
    report['mp3_decoded'] += 1
assert report['maps'] == 103
assert report['navigation_graphs'] == 89
report['scope'] = 'Host structural and libmpg123 decoder checks; target engine/gameplay unverified'
print(json.dumps(report, indent=2))
