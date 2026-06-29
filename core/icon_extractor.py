"""core/icon_extractor.py — 从安装包中提取图标。

支持从 .exe 和 .msi 文件中提取图标，并缓存到 tools/_icons/ 目录。
"""

from __future__ import annotations

import ctypes
import os
import struct
from pathlib import Path
from typing import Optional

try:
    import win32gui
    import win32ui
    import win32con
    import pywin32
    _HAS_PYWIN32 = True
except ImportError:
    _HAS_PYWIN32 = False

try:
    import msilib
    _HAS_MSILIB = True
except ImportError:
    _HAS_MSILIB = False


class IconExtractor:
    def __init__(self, cache_dir: Optional[Path] = None):
        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parent.parent / "tools" / "_icons"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def extract_icon(self, file_path: str, output_name: str) -> Optional[str]:
        """从文件中提取图标并保存到缓存目录。

        Args:
            file_path: 安装包文件路径
            output_name: 输出文件名（不含扩展名）

        Returns:
            缓存图标的路径，失败返回 None
        """
        ext = Path(file_path).suffix.lower()

        if ext == ".exe":
            return self._extract_exe_icon(file_path, output_name)
        elif ext == ".msi":
            return self._extract_msi_icon(file_path, output_name)
        else:
            return None

    def _extract_exe_icon(self, exe_path: str, output_name: str) -> Optional[str]:
        """从 .exe 文件中提取图标。"""
        if not os.path.exists(exe_path):
            return None

        output_path = self.cache_dir / f"{output_name}.png"
        if output_path.exists():
            return str(output_path)

        if _HAS_PYWIN32:
            result = self._extract_with_pywin32(exe_path, output_path)
            if result:
                return result
        
        result = self._extract_with_ctypes(exe_path, output_path)
        if result:
            return result
        
        return self._extract_with_subprocess(exe_path, output_path)

    def _extract_with_pywin32(self, exe_path: str, output_path: Path) -> Optional[str]:
        """使用 pywin32 提取图标。"""
        try:
            large, small = win32gui.ExtractIconEx(exe_path, 0)

            if large:
                hicon = large[0]
            elif small:
                hicon = small[0]
            else:
                return None

            hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
            hbmp = win32ui.CreateBitmap()
            hbmp.CreateCompatibleBitmap(hdc, 64, 64)
            hdc_mem = hdc.CreateCompatibleDC()
            hdc_mem.SelectObject(hbmp)

            win32gui.DrawIconEx(hdc_mem.GetHandleOutput(), 0, 0, hicon, 64, 64, 0, None, win32con.DI_NORMAL)

            bmpinfo = hbmp.GetInfo()
            bmpstr = hbmp.GetBitmapBits(True)

            png_data = self._bmp_to_png(bmpstr, bmpinfo["bmWidth"], bmpinfo["bmHeight"])
            if png_data and len(png_data) > 100:
                output_path.write_bytes(png_data)
                return str(output_path)

            win32gui.DestroyIcon(hicon)

        except Exception as e:
            pass

        return None

    def _extract_with_ctypes(self, exe_path: str, output_path: Path) -> Optional[str]:
        """使用 ctypes 提取图标。"""
        hicon = 0
        hdc = 0
        hdc_mem = 0
        bmp = 0
        old_bmp = 0
        
        try:
            shell32 = ctypes.windll.shell32
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32

            hicon = shell32.ExtractIconW(0, exe_path, 0)
            if hicon == 0 or hicon > 65535:
                return None

            hdc = user32.GetDC(0)
            if hdc == 0:
                if hicon:
                    user32.DestroyIcon(hicon)
                return None

            hdc_mem = gdi32.CreateCompatibleDC(hdc)
            if hdc_mem == 0:
                user32.ReleaseDC(0, hdc)
                if hicon:
                    user32.DestroyIcon(hicon)
                return None

            bmp = gdi32.CreateCompatibleBitmap(hdc, 64, 64)
            if bmp == 0:
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(0, hdc)
                if hicon:
                    user32.DestroyIcon(hicon)
                return None

            old_bmp = gdi32.SelectObject(hdc_mem, bmp)

            result = user32.DrawIconEx(hdc_mem, 0, 0, hicon, 64, 64, 0, 0, 0x0003)
            if result == 0:
                gdi32.SelectObject(hdc_mem, old_bmp)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(0, hdc)
                if hicon:
                    user32.DestroyIcon(hicon)
                gdi32.DeleteObject(bmp)
                return None

            bitmap_info = struct.pack("LLLLLLLLLL", 40, 64, 64, 1, 32, 0, 0, 0, 0, 0)
            
            gdi32.GetDIBits(hdc, bmp, 0, 64, None, bitmap_info, 0)
            
            buffer_size = 64 * 64 * 4
            buffer = ctypes.create_string_buffer(buffer_size)

            result = gdi32.GetDIBits(hdc, bmp, 0, 64, buffer, bitmap_info, 0)

            gdi32.SelectObject(hdc_mem, old_bmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc)
            if hicon:
                user32.DestroyIcon(hicon)
            gdi32.DeleteObject(bmp)

            if result == 0:
                return None

            png_data = self._rgba_to_png(buffer.raw, 64, 64)
            if png_data and len(png_data) > 100:
                output_path.write_bytes(png_data)
                return str(output_path)

        except Exception:
            try:
                if old_bmp:
                    gdi32.SelectObject(hdc_mem, old_bmp)
                if hdc_mem:
                    gdi32.DeleteDC(hdc_mem)
                if hdc:
                    user32.ReleaseDC(0, hdc)
                if hicon:
                    user32.DestroyIcon(hicon)
                if bmp:
                    gdi32.DeleteObject(bmp)
            except Exception:
                pass

        return None

    def _extract_with_subprocess(self, exe_path: str, output_path: Path) -> Optional[str]:
        """使用 subprocess 提取图标（进程隔离，更安全）。"""
        try:
            import subprocess
            import sys
            
            script = f'''
import ctypes
import struct
import zlib

def extract_icon(exe_path, output_path):
    hicon = 0
    hdc = 0
    hdc_mem = 0
    bmp = 0
    old_bmp = 0
    
    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        hicon = shell32.ExtractIconW(0, exe_path, 0)
        if hicon == 0 or hicon > 65535:
            return False

        hdc = user32.GetDC(0)
        if hdc == 0:
            if hicon:
                user32.DestroyIcon(hicon)
            return False

        hdc_mem = gdi32.CreateCompatibleDC(hdc)
        if hdc_mem == 0:
            user32.ReleaseDC(0, hdc)
            if hicon:
                user32.DestroyIcon(hicon)
            return False

        bmp = gdi32.CreateCompatibleBitmap(hdc, 64, 64)
        if bmp == 0:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc)
            if hicon:
                user32.DestroyIcon(hicon)
            return False

        old_bmp = gdi32.SelectObject(hdc_mem, bmp)

        result = user32.DrawIconEx(hdc_mem, 0, 0, hicon, 64, 64, 0, 0, 0x0003)
        if result == 0:
            gdi32.SelectObject(hdc_mem, old_bmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc)
            if hicon:
                user32.DestroyIcon(hicon)
            gdi32.DeleteObject(bmp)
            return False

        bitmap_info = struct.pack("LLLLLLLLLL", 40, 64, 64, 1, 32, 0, 0, 0, 0, 0)
        gdi32.GetDIBits(hdc, bmp, 0, 64, None, bitmap_info, 0)
        
        buffer_size = 64 * 64 * 4
        buffer = ctypes.create_string_buffer(buffer_size)

        result = gdi32.GetDIBits(hdc, bmp, 0, 64, buffer, bitmap_info, 0)

        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc)
        if hicon:
            user32.DestroyIcon(hicon)
        gdi32.DeleteObject(bmp)

        if result == 0:
            return False

        rgba_data = buffer.raw
        width = 64
        height = 64
        
        def png_chunk(chunk_type, data):
            length = struct.pack(">I", len(data))
            crc_data = chunk_type + data
            crc = struct.pack(">I", zlib.crc32(crc_data) & 0xffffffff)
            return length + chunk_type + data + crc

        png_signature = b"\\x89PNG\\r\\n\\x1a\\n"
        ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        ihdr = png_chunk(b"IHDR", ihdr_data)

        raw_data = b""
        stride = ((width * 32 + 31) // 32) * 4
        for y in range(height - 1, -1, -1):
            raw_data += b"\\x00"
            for x in range(width):
                idx = y * stride + x * 4
                if idx + 3 < len(rgba_data):
                    raw_data += bytes([rgba_data[idx + 2], rgba_data[idx + 1], rgba_data[idx], rgba_data[idx + 3]])

        compressed = zlib.compress(raw_data)
        idat = png_chunk(b"IDAT", compressed)
        iend = png_chunk(b"IEND", b"")

        png_data = png_signature + ihdr + idat + iend
        if len(png_data) > 100:
            with open(output_path, "wb") as f:
                f.write(png_data)
            return True
            
    except Exception:
        try:
            if old_bmp:
                gdi32.SelectObject(hdc_mem, old_bmp)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            if hdc:
                user32.ReleaseDC(0, hdc)
            if hicon:
                user32.DestroyIcon(hicon)
            if bmp:
                gdi32.DeleteObject(bmp)
        except Exception:
            pass
    return False

extract_icon(r"{exe_path}", r"{output_path}")
'''
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                timeout=10
            )
            if result.returncode == 0 and output_path.exists():
                return str(output_path)
        except Exception:
            pass
        return None

    def _extract_msi_icon(self, msi_path: str, output_name: str) -> Optional[str]:
        """从 .msi 文件中提取图标。"""
        if not _HAS_MSILIB or not os.path.exists(msi_path):
            return None

        output_path = self.cache_dir / f"{output_name}.png"
        if output_path.exists():
            return str(output_path)

        try:
            db = msilib.OpenDatabase(msi_path, msilib.MSIDBOPEN_READONLY)

            view = db.OpenView("SELECT Name, Data FROM Binary WHERE Name LIKE '%Icon%'")
            view.Execute(None)

            record = view.Fetch()
            while record:
                name = record.GetString(1)
                if "Icon" in name or "icon" in name:
                    data = record.GetStream(2)
                    if data:
                        icon_data = data.Read(-1)
                        png_data = self._ico_to_png(icon_data)
                        if png_data:
                            output_path.write_bytes(png_data)
                            return str(output_path)
                record = view.Fetch()

            view.Close()
            db.Close()

        except Exception:
            pass

        return None

    def _bmp_to_png(self, bmp_data: bytes, width: int, height: int) -> Optional[bytes]:
        """将 BMP 数据转换为 PNG。"""
        try:
            import zlib
            import struct

            def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
                length = struct.pack(">I", len(data))
                crc_data = chunk_type + data
                crc = struct.pack(">I", zlib.crc32(crc_data) & 0xffffffff)
                return length + chunk_type + data + crc

            png_signature = b"\x89PNG\r\n\x1a\n"

            ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
            ihdr = png_chunk(b"IHDR", ihdr_data)

            raw_data = b""
            for y in range(height - 1, -1, -1):
                raw_data += b"\x00"
                for x in range(width):
                    idx = (y * width + x) * 4
                    if idx + 3 < len(bmp_data):
                        raw_data += bytes([
                            bmp_data[idx + 2],
                            bmp_data[idx + 1],
                            bmp_data[idx],
                            bmp_data[idx + 3],
                        ])

            compressed = zlib.compress(raw_data)
            idat = png_chunk(b"IDAT", compressed)

            iend = png_chunk(b"IEND", b"")

            return png_signature + ihdr + idat + iend
        except Exception:
            return None

    def _rgba_to_png(self, rgba_data: bytes, width: int, height: int) -> Optional[bytes]:
        """将 RGBA 数据转换为 PNG。"""
        try:
            import zlib
            import struct

            def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
                length = struct.pack(">I", len(data))
                crc_data = chunk_type + data
                crc = struct.pack(">I", zlib.crc32(crc_data) & 0xffffffff)
                return length + chunk_type + data + crc

            png_signature = b"\x89PNG\r\n\x1a\n"

            ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
            ihdr = png_chunk(b"IHDR", ihdr_data)

            raw_data = b""
            stride = ((width * 32 + 31) // 32) * 4
            for y in range(height - 1, -1, -1):
                raw_data += b"\x00"
                for x in range(width):
                    idx = y * stride + x * 4
                    if idx + 3 < len(rgba_data):
                        raw_data += bytes([
                            rgba_data[idx + 2],
                            rgba_data[idx + 1],
                            rgba_data[idx],
                            rgba_data[idx + 3],
                        ])

            compressed = zlib.compress(raw_data)
            idat = png_chunk(b"IDAT", compressed)

            iend = png_chunk(b"IEND", b"")

            return png_signature + ihdr + idat + iend
        except Exception:
            return None

    def _ico_to_png(self, ico_data: bytes) -> Optional[bytes]:
        """将 ICO 数据转换为 PNG。"""
        try:
            if len(ico_data) < 6:
                return None

            num_icons = struct.unpack("<H", ico_data[4:6])[0]

            if num_icons == 0:
                return None

            offset = struct.unpack("<I", ico_data[6 + (num_icons - 1) * 16:6 + num_icons * 16])[0]

            bmp_header_size = struct.unpack("<I", ico_data[offset:offset + 4])[0]

            if bmp_header_size != 40:
                return None

            width = struct.unpack("<I", ico_data[offset + 4:offset + 8])[0]
            height = struct.unpack("<I", ico_data[offset + 8:offset + 12])[0] // 2
            bits_per_pixel = struct.unpack("<H", ico_data[offset + 14:offset + 16])[0]

            if bits_per_pixel != 32:
                return None

            data_offset = offset + bmp_header_size
            bmp_data = ico_data[data_offset:]

            return self._rgba_to_png(bmp_data, width, height)
        except Exception:
            return None

    def get_cached_icon(self, name: str) -> Optional[str]:
        """获取缓存的图标路径。"""
        icon_path = self.cache_dir / f"{name}.png"
        if icon_path.exists():
            return str(icon_path)
        return None

    def clear_cache(self) -> int:
        """清空缓存目录，返回删除的文件数。"""
        count = 0
        for f in self.cache_dir.iterdir():
            if f.is_file() and f.suffix == ".png":
                f.unlink()
                count += 1
        return count


def extract_and_cache_icon(file_path: str, output_name: str) -> Optional[str]:
    """便捷函数：提取图标并缓存。"""
    extractor = IconExtractor()
    return extractor.extract_icon(file_path, output_name)