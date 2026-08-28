"""
Entry point for the SCP GUI application.
Run with:  python3 -m scpgui.main
"""
import os
import sys
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from scpgui.main_window import MainWindow
from scpgui import theme

# Bump the default UI font up two points from the system default --
# the base size read here comes from Qt/the desktop theme, whatever
# that happens to be on a given machine.
FONT_SIZE_INCREASE = 2


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("scp gui")
    icon_paths = [
        "/usr/share/pixmaps/scp-gui.png",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "logo.png"),
    ]
    icon_path = next((path for path in icon_paths if os.path.exists(path)), None)
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    theme.apply_dark_theme(app)  # dark by default; toggle from the toolbar

    font = app.font()
    font.setPointSize(font.pointSize() + FONT_SIZE_INCREASE)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()