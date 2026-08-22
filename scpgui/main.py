"""
Entry point for the SCP GUI application.
Run with:  python3 -m scpgui.main
"""
import sys
from PyQt5.QtWidgets import QApplication

from scpgui.main_window import MainWindow
from scpgui import theme


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SCP GUI")
    theme.apply_dark_theme(app)  # dark by default; toggle from the toolbar
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
