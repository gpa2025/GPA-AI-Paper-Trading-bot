"""
Creates a desktop shortcut and icon for the GPA AI Paper Trading Bot.
Run: python create_shortcut.py
"""

import os
import sys
import struct


def create_ico(path):
    """Create a 32x32 ICO file with a green chart on dark background."""
    width, height = 32, 32
    pixels = []

    for y in range(height):
        for x in range(width):
            r, g, b, a = 15, 17, 23, 255

            # Rounded corners
            corner = 4
            in_corner = False
            for cx, cy in [(corner, corner), (width-1-corner, corner),
                           (corner, height-1-corner), (width-1-corner, height-1-corner)]:
                dx, dy = x - cx, y - cy
                if ((x < corner or x > width-1-corner) and
                    (y < corner or y > height-1-corner) and
                    dx*dx + dy*dy > corner*corner):
                    in_corner = True
            if in_corner:
                a = 0

            # Green chart line
            chart_y = {
                4: 22, 5: 21, 6: 20, 7: 19, 8: 18, 9: 17,
                10: 16, 11: 15, 12: 16, 13: 17, 14: 18,
                15: 16, 16: 14, 17: 12, 18: 10, 19: 8,
                20: 10, 21: 12, 22: 14, 23: 12, 24: 10,
                25: 8, 26: 6, 27: 7, 28: 8,
            }
            if x in chart_y and abs(y - chart_y[x]) <= 1:
                r, g, b = 34, 197, 94

            if 24 <= y <= 28 and 8 <= x <= 24:
                r, g, b = 139, 143, 163

            pixels.append((b, g, r, a))

    pixel_data = b''
    for py in range(height - 1, -1, -1):
        for px in range(width):
            b, g, r, a = pixels[py * width + px]
            pixel_data += struct.pack('BBBB', b, g, r, a)

    and_mask = b'\x00' * (((width + 31) // 32) * 4 * height)

    bmp_header = struct.pack('<IiiHHIIiiII',
        40, width, height * 2, 1, 32, 0,
        len(pixel_data) + len(and_mask), 0, 0, 0, 0)

    ico_header = struct.pack('<HHH', 0, 1, 1)
    data_offset = 6 + 16
    entry = struct.pack('<BBBBHHiI',
        width if width < 256 else 0,
        height if height < 256 else 0,
        0, 0, 1, 32,
        len(bmp_header) + len(pixel_data) + len(and_mask),
        data_offset)

    with open(path, 'wb') as f:
        f.write(ico_header + entry + bmp_header + pixel_data + and_mask)
    print(f"  [OK] Icon created: {path}")


def create_shortcut_com(bat_path, ico_path):
    """Create shortcut using Windows COM (primary method)."""
    try:
        import comtypes.client
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        shortcut_path = os.path.join(desktop, "GPA Trading Bot.lnk")

        ws = comtypes.client.CreateObject("WScript.Shell")
        sc = ws.CreateShortcut(shortcut_path)
        sc.TargetPath = bat_path
        sc.WorkingDirectory = os.path.dirname(bat_path)
        sc.IconLocation = ico_path
        sc.Description = "GPA AI Paper Trading Bot"
        sc.Save()
        print(f"  [OK] Shortcut created (COM): {shortcut_path}")
        return True
    except Exception:
        return False


def create_shortcut_powershell(bat_path, ico_path):
    """Create shortcut using PowerShell (fallback method)."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "GPA Trading Bot.lnk")
    work_dir = os.path.dirname(bat_path)

    # Escape backslashes for PowerShell
    ps_cmd = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$sc = $ws.CreateShortcut(\'{shortcut_path}\'); '
        f'$sc.TargetPath = \'{bat_path}\'; '
        f'$sc.WorkingDirectory = \'{work_dir}\'; '
        f'$sc.IconLocation = \'{ico_path}\'; '
        f'$sc.Description = \'GPA AI Paper Trading Bot\'; '
        f'$sc.Save()'
    )

    ret = os.system(f'powershell -ExecutionPolicy Bypass -Command "{ps_cmd}"')
    if ret == 0 and os.path.exists(shortcut_path):
        print(f"  [OK] Shortcut created (PowerShell): {shortcut_path}")
        return True
    return False


def create_shortcut_vbs(bat_path, ico_path):
    """Create shortcut using VBScript (last resort fallback)."""
    import tempfile
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "GPA Trading Bot.lnk")
    work_dir = os.path.dirname(bat_path)

    vbs_content = f'''Set ws = CreateObject("WScript.Shell")
Set sc = ws.CreateShortcut("{shortcut_path}")
sc.TargetPath = "{bat_path}"
sc.WorkingDirectory = "{work_dir}"
sc.IconLocation = "{ico_path}"
sc.Description = "GPA AI Paper Trading Bot"
sc.Save
'''
    vbs_path = os.path.join(tempfile.gettempdir(), "create_shortcut.vbs")
    with open(vbs_path, 'w') as f:
        f.write(vbs_content)

    ret = os.system(f'cscript //nologo "{vbs_path}"')
    try:
        os.remove(vbs_path)
    except OSError:
        pass

    if ret == 0 and os.path.exists(shortcut_path):
        print(f"  [OK] Shortcut created (VBScript): {shortcut_path}")
        return True
    return False


if __name__ == "__main__":
    print("\n  GPA AI Paper Trading Bot — Setup\n")

    project_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(project_dir, "trading_bot.ico")
    bat_path = os.path.join(project_dir, "launch_trading_bot.bat")

    # Create icon
    create_ico(ico_path)

    # Try three methods to create the shortcut
    ok = create_shortcut_com(bat_path, ico_path)
    if not ok:
        print("  [..] COM method failed, trying PowerShell...")
        ok = create_shortcut_powershell(bat_path, ico_path)
    if not ok:
        print("  [..] PowerShell method failed, trying VBScript...")
        ok = create_shortcut_vbs(bat_path, ico_path)
    if not ok:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        print(f"\n  [!!] Could not create shortcut automatically.")
        print(f"  Manual steps:")
        print(f"    1. Right-click desktop > New > Shortcut")
        print(f"    2. Location: {bat_path}")
        print(f"    3. Name: GPA Trading Bot")
        print(f"    4. Right-click shortcut > Properties > Change Icon > {ico_path}")

    print("\n  Done!\n")
