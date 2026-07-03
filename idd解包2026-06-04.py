#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
泡泡堂 (PopTag/Crazy Arcade) 资源提取工具 - 增强版
===================================================
- 完全兼容 IDX/IDD 的解密与文件名解析
- 自动识别常见文件格式 (BMP, PNG, JPG, GIF, WAV, OGG, MP3, TXT)
- 对未识别的 .bin 数据尝试解码为精灵图 (Sprite) 并输出 PNG/BMP
- 根据文件名前缀强制指定扩展名 (mp3→.mp3, help→.txt)

用法：
    python popart_extractor_enhanced.py

默认处理当前目录下的 Cx.idx/Cx.idd 和 Fx.idx/Fx.idd
输出到当前目录/extracted
"""

import struct
import os
import sys
import shutil

# ----------------------------- 常量定义 ---------------------------------
ZERO = 0
ONE = 1
TWO = 2
THREE = 3
FOUR = 4
EIGHT = 8
TWELVE = 12
SIXTEEN = 16

CTRL_TRANSPARENT = 0x80
CTRL_REPEAT      = 0x40
MASK_1F = 0x1F
MASK_3F = 0x3F

# ----------------------------- Pillow 检测 ----------------------------
try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# ----------------------------- 精灵图解码函数（源自 bnb_ca_unpack_fx_all_in_one）---
def rgb565_to_rgba(v):
    """RGB565 -> RGBA (alpha=255)"""
    r5 = (v >> 11) & MASK_1F
    g6 = (v >> 5)  & MASK_3F
    b5 = v & MASK_1F

    r = (r5 << 3) | (r5 >> 2)
    g = (g6 << 2) | (g6 >> 4)
    b = (b5 << 3) | (b5 >> 2)

    return (r, g, b, 255)

def parse_sprite_container(data):
    """
    解析精灵图容器格式
    返回: dict 或 None
    """
    size = len(data)
    if size < 16:
        return None

    frame_count = struct.unpack_from('<I', data, 0)[0]
    version     = struct.unpack_from('<I', data, 4)[0]
    width       = struct.unpack_from('<I', data, 8)[0]
    height      = struct.unpack_from('<I', data, 12)[0]

    # 合理性检查
    if frame_count <= 0 or frame_count > 100:
        return None
    if version != 1:
        return None
    if width <= 0 or height <= 0 or width > 10000 or height > 10000:
        return None

    pos = 16
    frames = []
    for i in range(frame_count):
        if pos + 28 > size:
            return None
        vals = []
        for j in range(7):
            vals.append(struct.unpack_from('<I', data, pos + j*4)[0])
        frames.append(vals)
        pos += 28

    if pos + 4 > size:
        return None
    payload_size = struct.unpack_from('<I', data, pos)[0]
    payload_start = pos + 4
    if payload_size != size - payload_start:
        return None
    payload = data[payload_start:]

    return {
        "frame_count": frame_count,
        "version": version,
        "width": width,
        "height": height,
        "frames": frames,
        "payload_size": payload_size,
        "payload": payload,
    }

def decode_payload(payload, width, height):
    """解压精灵图 RLE 数据 -> RGBA 像素列表"""
    total_pixels = width * height
    pixels = []
    pos = 0
    transparent_pixel = (0, 0, 0, 0)

    while pos < len(payload) and len(pixels) < total_pixels:
        ctrl = payload[pos]
        pos += 1

        if ctrl == 0:
            break

        if ctrl >= CTRL_TRANSPARENT:          # 透明填充
            count = ctrl - CTRL_TRANSPARENT
            for _ in range(count):
                pixels.append(transparent_pixel)

        elif ctrl >= CTRL_REPEAT:             # 重复同一颜色
            count = ctrl - CTRL_REPEAT
            if pos + 2 > len(payload):
                break
            color = payload[pos] | (payload[pos+1] << 8)
            pos += 2
            rgba = rgb565_to_rgba(color)
            for _ in range(count):
                pixels.append(rgba)

        else:                                 # 直接颜色数据
            count = ctrl
            for _ in range(count):
                if pos + 2 > len(payload):
                    break
                color = payload[pos] | (payload[pos+1] << 8)
                pos += 2
                pixels.append(rgb565_to_rgba(color))

    # 不足的补透明
    while len(pixels) < total_pixels:
        pixels.append(transparent_pixel)

    if len(pixels) > total_pixels:
        pixels = pixels[:total_pixels]

    return pixels

def write_bmp32(path, width, height, pixels):
    """将 RGBA 像素写入 32 位 BMP 文件（顶向下）"""
    file_header_size = 14
    info_header_size = 40
    bpp = 32
    row_size = width * 4
    pixel_data_size = row_size * height
    file_size = file_header_size + info_header_size + pixel_data_size

    with open(path, "wb") as f:
        # BITMAPFILEHEADER
        f.write(b"BM")
        f.write(struct.pack("<I", file_size))
        f.write(struct.pack("<HH", 0, 0))
        f.write(struct.pack("<I", file_header_size + info_header_size))

        # BITMAPINFOHEADER
        f.write(struct.pack("<I", info_header_size))
        f.write(struct.pack("<i", width))
        f.write(struct.pack("<i", -height))   # 顶部向下
        f.write(struct.pack("<H", 1))          # 平面数
        f.write(struct.pack("<H", bpp))        # 位深
        f.write(struct.pack("<I", 0))          # 无压缩
        f.write(struct.pack("<I", pixel_data_size))
        f.write(struct.pack("<i", 2835))       # 水平分辨率 (72 DPI)
        f.write(struct.pack("<i", 2835))       # 垂直分辨率
        f.write(struct.pack("<I", 0))          # 调色板颜色数
        f.write(struct.pack("<I", 0))          # 重要颜色数

        # 像素数据 (B,G,R,A)
        for r, g, b, a in pixels:
            f.write(bytes([b, g, r, a]))

def save_png(path, width, height, pixels):
    """使用 Pillow 保存 PNG"""
    if not HAS_PILLOW:
        return False
    img = Image.new("RGBA", (width, height))
    img.putdata(pixels)
    img.save(path)
    return True

# ----------------------------- IDX 解密与解析（源自 popart_extractor）---------
def decrypt_idx(data, key):
    """XOR 链式解密 IDX 文件数据"""
    size = len(data)
    num_dwords = size >> 2
    remainder = size & 3

    result = bytearray(size)

    if num_dwords > 0:
        # 第一个 DWORD
        enc_val = struct.unpack_from('<I', data, 0)[0]
        dec_val = key ^ enc_val
        struct.pack_into('<I', result, 0, dec_val)

        # 后续 DWORD
        for i in range(1, num_dwords):
            enc_val = struct.unpack_from('<I', data, i * 4)[0]
            prev_dec = struct.unpack_from('<I', result, (i - 1) * 4)[0]
            dec_val = prev_dec ^ enc_val
            struct.pack_into('<I', result, i * 4, dec_val)

        last_dec = struct.unpack_from('<I', result, (num_dwords - 1) * 4)[0]
    else:
        last_dec = key

    # 处理剩余字节
    if remainder > 0:
        offset = num_dwords * 4
        for i in range(remainder):
            result[offset + i] = data[offset + i] ^ ((last_dec >> (i * 8)) & 0xFF)

    return bytes(result)

def parse_idx(decrypted_data):
    """解析解密后的 IDX 数据，返回条目列表（含文件名、偏移、大小等）"""
    entries = []
    data = decrypted_data

    # 解析头部
    val0 = struct.unpack_from('<I', data, 0)[0]

    if val0 != 0:
        entry_count = val0
        offset = 4
        has_extra = False
    else:
        version = struct.unpack_from('<I', data, 4)[0]
        entry_count = struct.unpack_from('<I', data, 8)[0]
        offset = 12
        has_extra = (version == 15)

    for i in range(entry_count):
        if offset + 4 > len(data):
            break

        name_len = struct.unpack_from('<i', data, offset)[0]
        offset += 4

        if name_len <= 0 or name_len >= 0x80:
            break

        name = data[offset:offset + name_len].decode('ascii', errors='replace')
        offset += name_len

        file_offset = struct.unpack_from('<I', data, offset)[0]
        offset += 4
        file_size   = struct.unpack_from('<I', data, offset)[0]
        offset += 4
        reserved    = struct.unpack_from('<I', data, offset)[0]
        offset += 4

        extra = 0
        if has_extra:
            extra = struct.unpack_from('<I', data, offset)[0]
            offset += 4

        entries.append({
            'index': i,
            'name': name,
            'offset': file_offset,
            'size': file_size,
            'reserved': reserved,
            'extra': extra,
        })

    return entries

# ----------------------------- 文件类型检测 ---------------------------------
def detect_ext_from_header(data):
    """根据文件头检测扩展名（小端序）"""
    if len(data) < 4:
        return '.bin'

    # 波形
    if len(data) >= 12 and data.startswith(b'RIFF') and data[8:12] == b'WAVE':
        return '.wav'
    # JPEG
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    # PNG
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    # BMP
    if data.startswith(b'BM'):
        return '.bmp'
    # GIF
    if data.startswith(b'GIF8'):
        return '.gif'
    # OGG
    if data.startswith(b'OggS'):
        return '.ogg'
    # MP3 (ID3 或帧同步)
    if data.startswith(b'ID3') or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return '.mp3'
    # DDS
    if data.startswith(b'DDS '):
        return '.dds'
    return '.bin'

# ----------------------------- 主提取函数 ---------------------------------
def extract_all(idx_path, idd_path, output_dir):
    """提取单个 IDX/IDD 对的所有资源"""
    print(f"\n处理: {os.path.basename(idx_path)}")

    # 读取并解密 IDX
    with open(idx_path, 'rb') as f:
        idx_data = f.read()
    magic = struct.unpack_from('<I', idx_data, 0)[0]
    decrypted = decrypt_idx(idx_data, magic)

    # 解析条目
    entries = parse_idx(decrypted)
    print(f"  条目数: {len(entries)}")

    os.makedirs(output_dir, exist_ok=True)

    # 创建子目录
    raw_dir = os.path.join(output_dir, "raw_bin")
    sprite_dir = os.path.join(output_dir, "sprite_png")
    other_dir = output_dir   # 普通文件直接放在根目录
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(sprite_dir, exist_ok=True)

    idd_size = os.path.getsize(idd_path)
    extracted = 0
    skipped = 0
    sprite_decoded = 0

    with open(idd_path, 'rb') as idd_file:
        for entry in entries:
            name = entry['name']
            offset = entry['offset']
            size = entry['size']

            if size == 0 or offset + size > idd_size:
                skipped += 1
                continue

            idd_file.seek(offset)
            data = idd_file.read(size)

            # ---------- 1. 根据文件名前缀强制指定扩展名 ----------
            lower_name = name.lower()
            if lower_name.startswith('mp3'):
                ext = '.mp3'
            elif lower_name.startswith('help'):
                ext = '.txt'
            else:
                # 2. 根据文件头检测
                ext = detect_ext_from_header(data)

            # 安全处理文件名
            safe_name = name
            for ch in '<>:"/\\|?*':
                safe_name = safe_name.replace(ch, '_')

            # ---------- 3. 对于未识别的 .bin 尝试解码为精灵图 ----------
            if ext == '.bin':
                sprite = parse_sprite_container(data)
                if sprite is not None:
                    # 解码精灵图
                    width = sprite['width']
                    height = sprite['height']
                    pixels = decode_payload(sprite['payload'], width, height)

                    # 保存为 PNG（首选）或 BMP
                    png_name = safe_name + '.png'
                    png_path = os.path.join(sprite_dir, png_name)
                    if save_png(png_path, width, height, pixels):
                        print(f"    [精灵图] {name} -> {png_name} (PNG)")
                        sprite_decoded += 1
                        extracted += 1
                        continue   # 已保存 PNG，跳过后续保存原始数据
                    else:
                        # 无 Pillow，保存 BMP
                        bmp_name = safe_name + '.bmp'
                        bmp_path = os.path.join(sprite_dir, bmp_name)
                        write_bmp32(bmp_path, width, height, pixels)
                        print(f"    [精灵图] {name} -> {bmp_name} (BMP)")
                        sprite_decoded += 1
                        extracted += 1
                        continue

                # 不是精灵图，保存原始 .bin 到 raw_bin 目录
                output_path = os.path.join(raw_dir, safe_name + ext)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, 'wb') as out:
                    out.write(data)
                extracted += 1
                continue

            # ---------- 4. 普通文件直接保存 ----------
            output_path = os.path.join(other_dir, safe_name + ext)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'wb') as out:
                out.write(data)
            extracted += 1

    print(f"  提取: {extracted} 个文件, 跳过: {skipped} 个")
    print(f"  其中精灵图解码: {sprite_decoded} 个 (保存在 {sprite_dir})")
    return extracted

# ----------------------------- 主程序 ---------------------------------
def main():
    # 使用当前工作目录作为基础目录
    base_dir = os.getcwd()
    output_base = os.path.join(base_dir, "extracted")

    # 检查必需的 Cx.idd 是否存在
    required_idd = os.path.join(base_dir, "Cx.idd")
    if not os.path.exists(required_idd):
        print("当前目录无法解析：找不到 Cx.idd 文件。")
        print(f"请确保脚本位于包含 Cx.idx / Cx.idd 的泡泡堂资源目录下。")
        sys.exit(1)

    # 可选：同时检查 Cx.idx 是否存在
    required_idx = os.path.join(base_dir, "Cx.idx")
    if not os.path.exists(required_idx):
        print("警告：找不到 Cx.idx 文件，可能无法正常提取。")

    # 清理旧输出（谨慎）
    if os.path.exists(output_base):
        shutil.rmtree(output_base)

    pairs = [
        ('Cx.idx', 'Cx.idd', 'Cx'),
        ('fx\Fx.idx', 'fx\Fx.idd', 'Fx'),
    ]

    total = 0
    for idx_name, idd_name, output_name in pairs:
        idx_path = os.path.join(base_dir, idx_name)
        idd_path = os.path.join(base_dir, idd_name)
        output_dir = os.path.join(output_base, output_name)

        if not os.path.exists(idx_path) or not os.path.exists(idd_path):
            print(f"跳过缺失文件: {idx_name} 或 {idd_name}")
            continue

        count = extract_all(idx_path, idd_path, output_dir)
        total += count

    print(f"\n{'='*50}")
    print(f"总计提取: {total} 个文件")
    print(f"输出目录: {output_base}")
    print(f"{'='*50}")

    if not HAS_PILLOW:
        print("\n提示: 未安装 Pillow，精灵图已输出为 BMP 格式。")
        print("如需 PNG，请运行: pip install pillow")

if __name__ == '__main__':
    main()