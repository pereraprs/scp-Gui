"""
theme.py
---------
Dark / light theme switching via QPalette + the "Fusion" style, which
renders consistently across Debian/Kali desktop environments (unlike
relying on the native GTK/Qt theme, which varies a lot host to host).
"""

from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtWidgets import QApplication, QStyleFactory
from PyQt5.QtCore import Qt


def apply_dark_theme(app: QApplication):
    app.setStyle(QStyleFactory.create("Fusion"))

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(37, 37, 38))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(25, 25, 26))
    palette.setColor(QPalette.AlternateBase, QColor(45, 45, 48))
    palette.setColor(QPalette.ToolTipBase, QColor(220, 220, 220))
    palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 120))
    palette.setColor(QPalette.Button, QColor(53, 53, 55))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 120))
    palette.setColor(QPalette.BrightText, QColor(255, 80, 80))
    palette.setColor(QPalette.Link, QColor(64, 180, 255))
    palette.setColor(QPalette.Highlight, QColor(38, 110, 165))
    palette.setColor(QPalette.HighlightedText, Qt.white)

    app.setPalette(palette)
    app.setStyleSheet("""
        QToolTip { color: #dcdcdc; background-color: #2d2d30; border: 1px solid #555; }
        QTreeView, QTreeWidget { background-color: #191a1a; alternate-background-color: #232324; }
        QHeaderView::section { background-color: #2d2d30; color: #dcdcdc; padding: 4px; border: 1px solid #191a1a; }
        QPushButton { background-color: #3a3a3d; border: 1px solid #555; padding: 4px 10px; border-radius: 3px; }
        QPushButton:hover { background-color: #46464a; }
        QPushButton:disabled { color: #777; }
        QLineEdit, QSpinBox, QComboBox { background-color: #1e1e1f; border: 1px solid #555; padding: 2px; border-radius: 3px; }
        QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; }
        QProgressBar::chunk { background-color: #2e9d5b; }
    """)


def apply_light_theme(app: QApplication):
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet("""
        QProgressBar::chunk { background-color: #2e9d5b; }
    """)
