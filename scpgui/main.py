"""
Entry point for the SCP GUI application.
Run with:  python3 -m scpgui.main
"""
import sys
from PyQt5.QtWidgets import QApplication

from scpgui.main_window import MainWindow
from scpgui import theme

# Bump the default UI font up two points from the system default --
# the base size read here comes from Qt/the desktop theme, whatever
# that happens to be on a given machine.
FONT_SIZE_INCREASE = 2


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SCP GUI")
    theme.apply_dark_theme(app)  # dark by default; toggle from the toolbar

    font = app.font()
    font.setPointSize(font.pointSize() + FONT_SIZE_INCREASE)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()