"""Cross-platform notification CLI.

Usage:
    notify "Title" "Body"
    echo "message" | notify "Title"
    some-command | notify "Title" -
"""

import sys
import platform
import subprocess
import click


def send_notification(title: str, body: str) -> bool:
    """Send a desktop notification using native OS tools."""
    system = platform.system()

    try:
        if system == "Linux":
            # Try notify-send (most common)
            subprocess.run(["notify-send", title, body], check=True)
        elif system == "Darwin":  # macOS
            # Use osascript for notifications
            script = f'display notification "{body}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=True)
        elif system == "Windows":
            # Use PowerShell toast notification
            ps_script = f"""
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
            $template = @"
            <toast>
                <visual>
                    <binding template="ToastText02">
                        <text id="1">{title}</text>
                        <text id="2">{body}</text>
                    </binding>
                </visual>
            </toast>
"@
            $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
            $xml.LoadXml($template)
            $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Agent Sommelier").Show($toast)
            """
            subprocess.run(
                ["powershell", "-Command", ps_script], check=True, capture_output=True
            )
        else:
            click.echo(f"Unsupported platform: {system}", err=True)
            return False
        return True
    except subprocess.CalledProcessError as e:
        click.echo(f"Failed to send notification: {e}", err=True)
        return False
    except FileNotFoundError as e:
        click.echo(f"Notification tool not found: {e}", err=True)
        return False


@click.command()
@click.argument("title")
@click.argument("body", required=False, default=None)
@click.option(
    "--sound", is_flag=True, help="Play notification sound (platform dependent)"
)
def main(title: str, body: str | None, sound: bool):
    """Send a desktop notification.

    TITLE: Notification title

    BODY: Notification body (optional, can be piped)

    Examples:
        notify "Alert" "Something happened!"
        echo "Status update" | notify "Progress"
        cat log.txt | notify "Logs" -
    """
    # If body is None or "-", read from stdin
    if body is None or body == "-":
        if not sys.stdin.isatty():
            body = sys.stdin.read().strip()
        else:
            body = ""

    if not body:
        body = title
        title = "Notification"

    success = send_notification(title, body)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
