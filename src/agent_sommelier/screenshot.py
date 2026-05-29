"""Screenshot CLI - uses MSS for Windows (all monitors), native tools elsewhere."""

import sys
import platform
import subprocess
from pathlib import Path
from datetime import datetime
import tempfile
import click

from agent_sommelier import __version__


def get_virtual_screen_bounds():
    """Get the bounds of all monitors combined (virtual screen)."""
    if platform.system() != "Windows":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
        left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

        # Sometimes these return 0, fallback to primary screen
        if width <= 0 or height <= 0:
            return None

        return (left, top, left + width, top + height)
    except:
        return None


def screenshot_windows_mss(output_path: str, verbose: bool = False, primary: bool = False) -> bool:
    """Take screenshot using MSS library (cross-platform, all monitors)."""
    try:
        import mss
        import mss.tools

        with mss.mss() as sct:
            if primary:
                # Monitor 1 = primary monitor
                monitor = sct.monitors[1]
            else:
                # Monitor 0 = all monitors combined
                monitor = sct.monitors[0]
            sct_img = sct.grab(monitor)
            mss.tools.to_png(sct_img.rgb, sct_img.size, output=output_path)
            return True
    except Exception as e:
        if verbose:
            click.echo(f"MSS failed: {e}", err=True)
        return False


def screenshot_windows_pil(output_path: str, primary: bool = False) -> bool:
    """Take screenshot using PIL ImageGrab (pure Python, no PowerShell)."""
    try:
        from PIL import ImageGrab

        if primary:
            # Capture primary monitor only
            img = ImageGrab.grab()
        else:
            # Try to get virtual screen bounds (all monitors)
            bbox = get_virtual_screen_bounds()

            if bbox:
                # Capture the entire virtual screen
                img = ImageGrab.grab(bbox=bbox)
            else:
                # Fallback: capture default (usually primary)
                img = ImageGrab.grab()

        # Save
        img.save(output_path)
        return True

    except Exception as e:
        click.echo(f"PIL ImageGrab failed: {e}", err=True)
        return False


def screenshot_native(output_path: str, verbose: bool = False, primary: bool = False) -> bool:
    """Take screenshot using native OS tools (no PowerShell)."""
    system = platform.system()

    try:
        if system == "Linux":
            # Try various screenshot tools
            if primary:
                tools_to_try = [("gnome-screenshot", ["gnome-screenshot", "-f", output_path]),
                                ("scrot", ["scrot", output_path])]
                for tool_name, tool_cmd in tools_to_try:
                    try:
                        subprocess.run(tool_cmd, check=True, capture_output=True)
                        return True
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        continue
                return False
            else:
                for tool in ["gnome-screenshot", "scrot", "import", "flameshot"]:
                    try:
                        if tool == "gnome-screenshot":
                            subprocess.run(
                                [tool, "-f", output_path], check=True, capture_output=True
                            )
                        elif tool == "scrot":
                            subprocess.run(
                                [tool, output_path], check=True, capture_output=True
                            )
                        elif tool == "import":
                            subprocess.run(
                                [tool, "-window", "root", output_path],
                                check=True,
                                capture_output=True,
                            )
                        elif tool == "flameshot":
                            subprocess.run(
                                [tool, "full", "-p", output_path],
                                check=True,
                                capture_output=True,
                            )
                        return True
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        continue
                return False

        elif system == "Darwin":  # macOS
            args = ["screencapture"]
            if primary:
                args.append("-m")  # -m captures only the main monitor
            args.append(output_path)
            subprocess.run(args, check=True)
            return True

        elif system == "Windows":
            # Try MSS first (works better for all monitors), then fallback to PIL
            if screenshot_windows_mss(output_path, verbose=verbose, primary=primary):
                return True
            return screenshot_windows_pil(output_path, primary=primary)

        else:
            return False

    except Exception as e:
        click.echo(f"Screenshot failed: {e}", err=True)
        return False


def copy_to_clipboard_windows(image_path: str) -> bool:
    """Copy image file to Windows clipboard using PowerShell."""
    try:
        abs_path = str(Path(image_path).resolve())
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            f"[System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile('{abs_path}'))"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def auto_name_screenshot() -> Path:
    """Generate auto-named screenshot path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"screenshot_{timestamp}.png"

    # Use standard temp directory
    temp_dir = Path(tempfile.gettempdir()) / "agentcli_screenshots"
    temp_dir.mkdir(parents=True, exist_ok=True)

    return temp_dir / filename


@click.command()
@click.version_option(__version__, prog_name="screenshot")
@click.argument("output", required=False, default=None, type=click.Path())
@click.option(
    "--all-monitors", is_flag=True, help="Capture all monitors (default behavior)"
)
@click.option("--primary", is_flag=True, help="Capture only the primary monitor")
@click.option("--clipboard", is_flag=True, help="Copy screenshot to clipboard")
@click.option("--verbose", is_flag=True, help="Show detailed output")
def main(output: str | None, all_monitors: bool, primary: bool, clipboard: bool, verbose: bool):
    """Take a screenshot.

    OUTPUT: Optional output file path. If not provided, auto-generates name.

    Examples:
        screenshot              # Auto-names, outputs path
        screenshot shot.png     # Save to shot.png
        screenshot --primary    # Primary monitor only
        screenshot --clipboard  # Copy to clipboard
    """
    if output:
        output_path = Path(output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_path = auto_name_screenshot()

    # Use MSS on Windows (captures all monitors), fallback to PIL
    success = screenshot_native(str(output_path), verbose=verbose, primary=primary)

    if success and clipboard:
        if sys.platform == "win32":
            if not copy_to_clipboard_windows(str(output_path)):
                click.echo("Warning: Failed to copy to clipboard.", err=True)
        else:
            click.echo("Warning: Clipboard copy not supported on this platform.", err=True)

    if success:
        click.echo(str(output_path))
    else:
        click.echo("Failed to take screenshot.", err=True)
        click.echo(
            "Install with: uv tool install agent-sommelier-cli[screenshot]", err=True
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
