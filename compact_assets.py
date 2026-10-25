#!/usr/bin/env python3
"""Build a private low-detail campaign pack from an installed Half-Life copy.

Never edits the source or installs anything on the Station. Geometry, collision,
scripts, model animation and looping WAVs are retained. BSP texture dimensions
stay original; Xash loads lower-resolution pixels from the generated WADs.
"""
import argparse
import collections
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import wave
import zipfile

from PIL import Image

CAMPAIGN = re.compile(r"(?:c\da\w*|t0a\w*)\.bsp", re.I)


def lump_data(data):
    assert struct.unpack_from('<i', data)[0] == 30, 'Expected GoldSrc BSP30'
    lumps = []
    for index in range(15):
        offset, length = struct.unpack_from('<ii', data, 4 + index * 8)
        assert 0 <= offset <= len(data) and 0 <= length <= len(data) - offset
        lumps.append(data[offset:offset + length])
    return lumps


def wad_entries(data):
    magic, count, directory = struct.unpack_from('<4sii', data)
    assert magic == b'WAD3'
    result = {}
    for index in range(count):
        offset, size, rawsize, kind, compression, _, name = struct.unpack_from(
            '<iiiBBH16s', data, directory + index * 32)
        if kind == 67:
            assert compression == 0 and size == rawsize
            result[name.split(b'\0', 1)[0].lower()] = data[offset:offset + size]
    return result


def write_wad(entries):
    data = bytearray(b'WAD3' + bytes(8))
    directory = bytearray()
    for name, texture in sorted(entries.items()):
        directory += struct.pack('<iiiBBH16s', len(data), len(texture), len(texture),
                                 67, 0, 0, name)
        data += texture
    struct.pack_into('<ii', data, 4, len(entries), len(data))
    return bytes(data + directory)


def small_texture(data, limit, colors=32, texture_filter='nearest'):
    name, width, height, *offsets = struct.unpack_from('<16s6I', data)
    assert width > 0 and height > 0 and offsets[0] >= 40
    palette_at = offsets[3] + (width >> 3) * (height >> 3)
    assert struct.unpack_from('<H', data, palette_at)[0] == 256
    palette = data[palette_at + 2:palette_at + 770]
    assert len(palette) == 768
    pixels = data[offsets[0]:offsets[0] + width * height]
    assert len(pixels) == width * height
    image = Image.frombytes('P', (width, height), pixels)
    image.putpalette(palette)
    # Keep integer power-of-two reductions and a minimum 16-texel side.
    factor = 1
    while max(width // factor, height // factor) > limit and min(width // factor, height // factor) > 16:
        factor *= 2
    new_width, new_height = max(16, width // factor), max(16, height // factor)
    mask = image.resize((new_width, new_height), Image.Resampling.NEAREST)
    # Average source texels before quantizing; nearest-neighbour shrinking
    # aliases fine patterns and can change the apparent colour of a surface.
    resample = Image.Resampling.BOX if texture_filter == 'box' else Image.Resampling.NEAREST
    image = image.convert('RGB').resize((new_width, new_height), resample)
    quantized = image.quantize(colors=min(colors, 255) if name.startswith(b'{') else colors,
                               dither=Image.Dither.NONE)
    if name.startswith(b'{'):
        pixels = bytearray(quantized.tobytes())
        for pos, old in enumerate(mask.tobytes()):
            if old == 255:
                pixels[pos] = 255
        quantized.frombytes(bytes(pixels))
    new_palette = bytearray(quantized.getpalette()[:768])
    new_palette += bytes(768 - len(new_palette))
    new_palette[765:768] = palette[765:768]
    palette = bytes(new_palette)
    name = name.split(b'\0', 1)[0].lower().ljust(16, b'\0')
    out = bytearray(struct.pack('<16s6I', name, new_width, new_height, 0, 0, 0, 0))
    for level in range(4):
        struct.pack_into('<I', out, 24 + level * 4, len(out))
        out += quantized.resize((new_width >> level, new_height >> level), Image.Resampling.NEAREST).tobytes()
    out += struct.pack('<H', 256) + palette
    return bytes(out)


def compact_map(path, shared, limit, light_bits, texture_colors=32, texture_filter='nearest'):
    original = path.read_bytes()
    lumps = lump_data(original)
    textures = lumps[2]
    count, = struct.unpack_from('<i', textures)
    new_textures = bytearray(struct.pack('<i', count) + bytes(count * 4))
    wad = {}
    for index in range(count):
        offset, = struct.unpack_from('<i', textures, 4 + index * 4)
        if offset == -1:
            struct.pack_into('<i', new_textures, 4 + index * 4, -1)
            continue
        header = textures[offset:offset + 40]
        assert len(header) == 40
        name, width, height, first, *_ = struct.unpack('<16s6I', header)
        key = name.split(b'\0', 1)[0].lower()
        raw = textures[offset:] if first else shared.get(key)
        assert raw is not None, f'{path.name}: missing texture {key!r}'
        wad[key] = small_texture(raw, limit, texture_colors, texture_filter)
        struct.pack_into('<i', new_textures, 4 + index * 4, len(new_textures))
        # Original dimensions are retained for UV projection AND lightmapping.
        new_textures += header[:24] + bytes(16)
    lumps[2] = bytes(new_textures)
    wad_name = f's_{path.stem}.wad'
    entities = lumps[0]
    assert b'"wad"' in entities, f'{path}: missing worldspawn WAD list'
    lumps[0], edits = re.subn(rb'"wad"\s*"[^"]*"', b'"wad" "' + wad_name.encode() + b'"', entities, count=1)
    assert edits == 1
    if light_bits < 8:
        levels = (1 << light_bits) - 1
        table = bytes(round(round(value * levels / 255) * 255 / levels) for value in range(256))
        lumps[8] = lumps[8].translate(table)
    output = bytearray(124)
    struct.pack_into('<i', output, 0, 30)
    for index, content in enumerate(lumps):
        output += bytes((-len(output)) % 4)
        struct.pack_into('<ii', output, 4 + index * 8, len(output), len(content))
        output += content
    # All geometry, collision, PVS, texinfo and face offsets must be identical.
    rebuilt = lump_data(output)
    old = lump_data(original)
    for index in set(range(15)) - {0, 2, 8}:
        assert rebuilt[index] == old[index], (path.name, index)
    assert re.sub(rb'"wad"\s*"[^"]*"', b'', rebuilt[0], count=1) == re.sub(
        rb'"wad"\s*"[^"]*"', b'', old[0], count=1)
    targets = [m.decode().lower() for m in re.findall(rb'"map"\s*"([^"]+)"', entities)]
    return bytes(output), wad_name, write_wad(wad), targets


def compact_model(data, colors):
    if data[:4] != b'IDST' or len(data) < 244:
        return data
    output = bytearray(data)
    count, directory = struct.unpack_from('<ii', data, 180)
    for index in range(count):
        name, flags, width, height, offset = struct.unpack_from('<64s4i', data, directory + index * 80)
        size = width * height
        assert 0 < size and 0 <= offset <= len(data) - size - 768
        image = Image.frombytes('P', (width, height), data[offset:offset + size])
        image.putpalette(data[offset + size:offset + size + 768])
        # Preserve transparent palette entry 255 for STUDIO_NF_MASKED skins.
        quantized = image.convert('RGB').quantize(colors=colors, dither=Image.Dither.NONE)
        pixels = bytearray(quantized.tobytes())
        if flags & 64:
            for pos, original in enumerate(data[offset:offset + size]):
                if original == 255:
                    pixels[pos] = 255
        palette = bytearray(quantized.getpalette()[:768])
        palette += bytes(768 - len(palette))
        palette[765:768] = data[offset + size + 765:offset + size + 768]
        output[offset:offset + size] = pixels
        output[offset + size:offset + size + 768] = palette
    return bytes(output)


def wav_chunks(data):
    assert data[:4] == b'RIFF' and data[8:12] == b'WAVE'
    position = 12
    while position + 8 <= len(data):
        name, size = struct.unpack_from('<4sI', data, position)
        yield name, data[position + 8:position + 8 + size]
        position += 8 + size + (size & 1)


def compact_sound(source, destination):
    data = source.read_bytes()
    chunks = dict(wav_chunks(data))
    # Do not change loop boundaries, pauses in sentences, or very short effects.
    if b'cue ' in chunks or b'smpl' in chunks:
        return 'loop-original'
    try:
        wav = wave.open(io.BytesIO(data))
        duration = wav.getnframes() / wav.getframerate()
    except wave.Error:
        return 'format-original'
    if duration < 0.4:
        return 'short-original'
    encoded_path = destination.with_suffix('.encode.mp3')
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source),
                    '-map_metadata', '-1', '-ac', '1', '-ar', '11025', '-c:a', 'libmp3lame',
                    '-b:a', '16k', '-threads', '1', str(encoded_path)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    mp3 = encoded_path.read_bytes()
    encoded_path.unlink()
    # Seekable MP3 output retains Xing/LAME gapless sample information. FFmpeg's
    # WAV muxer drops it and adds about 100 ms of leading silence, so wrap the
    # complete MP3 stream ourselves. 'fact' retains the original sample count.
    samples = round(wav.getnframes() * 11025 / wav.getframerate())
    fmt = struct.pack('<HHIIHH', 85, 1, 11025, 2000, 1, 0)
    chunks_out = [(b'fmt ', fmt), (b'fact', struct.pack('<I', samples)), (b'data', mp3)]
    encoded = b'WAVE' + b''.join(key + struct.pack('<I', len(content)) + content +
                               bytes(len(content) & 1) for key, content in chunks_out)
    encoded = b'RIFF' + struct.pack('<I', len(encoded)) + encoded
    # Xash WAV loader already recognizes format 85 (MP3-in-WAV).
    if len(encoded) >= len(data):
        return 'not-smaller-original'
    destination.write_bytes(encoded)
    return 'mp3-in-wav'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--texture-size', type=int, default=32)
    parser.add_argument('--texture-colors', type=int, choices=range(2, 257), default=32)
    parser.add_argument('--texture-filter', choices=['nearest', 'box'], default='nearest')
    parser.add_argument('--light-bits', type=int, choices=range(3, 9), default=4)
    parser.add_argument('--model-colors', type=int, default=32)
    parser.add_argument('--music', choices=['keep', 'low', 'omit'], default='low')
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    assert source.is_dir() and not output.exists() and source not in output.parents
    output.mkdir(parents=True)
    shared = {}
    for name in ['halflife.wad', 'xeno.wad', 'liquids.wad']:
        for key, raw in wad_entries((source / name).read_bytes()).items():
            shared.setdefault(key, raw)
    campaign = sorted(p for p in (source / 'maps').glob('*.bsp') if CAMPAIGN.fullmatch(p.name))
    assert len(campaign) == 103, f'Expected 103 campaign/training maps, found {len(campaign)}'
    targets = set()
    map_wads = []
    for path in campaign:
        data, wad_name, wad, links = compact_map(path, shared, args.texture_size, args.light_bits,
                                              args.texture_colors, args.texture_filter)
        (output / 'maps').mkdir(exist_ok=True)
        (output / 'maps' / path.name).write_bytes(data)
        map_wads.append((wad_name, wad_entries(wad)))
        targets.update(links)
    missing = targets - {path.stem.lower() for path in campaign}
    assert not missing, f'Missing transition destinations: {missing}'
    # Share identical textures across maps, retaining per-map variants when a
    # texture name has different pixels. The last WAD in the list takes priority.
    variants = collections.defaultdict(collections.Counter)
    payloads = {}
    for _, entries in map_wads:
        for key, texture in entries.items():
            digest = hashlib.sha256(texture).digest()
            variants[key][digest] += 1
            payloads[key, digest] = texture
    common = {key: payloads[key, counts.most_common(1)[0][0]] for key, counts in variants.items()}
    (output / 'station.wad').write_bytes(write_wad(common))
    for name, entries in map_wads:
        overrides = {key: value for key, value in entries.items() if value != common[key]}
        if overrides:
            (output / name).write_bytes(write_wad(overrides))
        path = output / 'maps' / (name[2:-4] + '.bsp')
        lumps = lump_data(path.read_bytes())
        replacement = b'station.wad' + (b';' + name.encode() if overrides else b'')
        lumps[0] = lumps[0].replace(name.encode(), replacement)
        rebuilt = bytearray(124)
        struct.pack_into('<i', rebuilt, 0, 30)
        for index, content in enumerate(lumps):
            rebuilt += bytes((-len(rebuilt)) % 4)
            struct.pack_into('<ii', rebuilt, 4 + index * 8, len(rebuilt), len(content))
            rebuilt += content
        path.write_bytes(rebuilt)
    print(f'Preserved {len(campaign)} BSP maps; checked {len(targets)} transition destinations', flush=True)
    # Preserve existing campaign AI navigation graphs. These are required for
    # NPC pathfinding; they must not be discarded with multiplayer map files.
    for graph in (source / 'maps' / 'graphs').glob('*.nod'):
        if (output / 'maps' / (graph.stem + '.bsp')).is_file():
            destination = output / 'maps' / 'graphs' / graph.name
            destination.parent.mkdir(exist_ok=True)
            shutil.copy2(graph, destination)
            stamp = (output / 'maps' / (graph.stem + '.bsp')).stat().st_mtime
            os.utime(destination, (stamp, stamp))
    audio_jobs = []
    copied = collections.Counter()
    for path in sorted(source.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        folder = relative.parts[0]
        extension = path.suffix.lower()
        if folder in {'maps', 'overviews', 'cl_dlls', 'dlls', 'controller_configs', 'resource', 'logos'}:
            continue
        if relative.parts[:2] == ('models', 'player'):
            # Multiplayer player choices. The campaign's models/player.mdl stays.
            continue
        if extension in {'.so', '.dll', '.dylib', '.pk3'} or path.name.lower() in {'halflife.wad', 'xeno.wad', 'liquids.wad'}:
            continue
        if folder == 'media' and (extension != '.mp3' or args.music == 'omit'):
            continue
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if folder == 'sound' and extension == '.wav':
            audio_jobs.append((path, destination))
        elif extension == '.mdl':
            destination.write_bytes(compact_model(path.read_bytes(), args.model_colors))
        elif relative.parts[:2] == ('gfx', 'env') and extension in {'.tga', '.bmp'}:
            image = Image.open(path)
            image.thumbnail((64, 64), Image.Resampling.LANCZOS)
            image.save(destination)
        elif folder == 'media' and args.music == 'low':
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(path),
                            '-map_metadata', '-1', '-ac', '1', '-ar', '11025', '-c:a', 'libmp3lame',
                            '-b:a', '16k', '-write_xing', '0', str(destination)], check=True)
        else:
            shutil.copy2(path, destination)
        copied[folder] += 1
    def encode(job):
        source_wav, destination = job
        mode = compact_sound(source_wav, destination)
        if mode != 'mp3-in-wav':
            shutil.copy2(source_wav, destination)
        return mode
    print(f'Encoding {len(audio_jobs)} sounds on the host', flush=True)
    audio = collections.Counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for number, mode in enumerate(pool.map(encode, audio_jobs), 1):
            audio[mode] += 1
            if number % 250 == 0:
                print(f'  audio {number}/{len(audio_jobs)}', flush=True)
    archive = output.with_suffix('.pk3')
    sizes = collections.defaultdict(lambda: [0, 0, 0])
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as pack:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                relative = path.relative_to(output).as_posix()
                pack.write(path, relative)
                entry = pack.getinfo(relative)
                group = 'map-textures' if relative.startswith('s_') and relative.endswith('.wad') else relative.split('/')[0]
                sizes[group][0] += entry.file_size
                sizes[group][1] += entry.compress_size
                sizes[group][2] += 1
    with zipfile.ZipFile(archive) as pack:
        assert pack.testzip() is None
    report = {'source': str(source), 'archive': str(archive), 'archive_bytes': archive.stat().st_size,
              'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'campaign_maps': len(campaign),
              'transition_destinations': sorted(targets), 'audio': dict(audio), 'groups': dict(sizes),
              'settings': {'texture_size': args.texture_size, 'texture_colors': args.texture_colors,
                           'texture_filter': args.texture_filter, 'light_bits': args.light_bits,
                           'model_colors': args.model_colors, 'music': args.music},
              'validation': 'Structural checks only; target engine/gameplay not yet verified'}
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
