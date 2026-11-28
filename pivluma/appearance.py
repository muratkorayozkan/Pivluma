"""Application and chart colors for the desktop theme settings."""

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase


def install_brand_font():
    if "Kalam" not in QFontDatabase.families():
        QFontDatabase.addApplicationFont(str(Path(__file__).with_name("fonts") / "Kalam-Bold.ttf"))


def animations_enabled():
    if sys.platform != "win32":
        return True
    enabled = ctypes.c_int()
    result = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    return bool(result and enabled.value)


THEMES = {
    "system": "System",
    "light": "Light",
    "dark-purple": "Dark purple",
    "dark-blue": "Dark blue",
    "dark-black": "Black",
    "light-warm": "Warm light",
    "light-mint": "Mint light",
    "dark-teal": "Dark teal",
    "dark-warm": "Warm dark",
    "dark-glass": "Midnight",
}

# Background, surface, text, border, history line, forecast line, forecast fill.
COLORS = {
    "light": ("#f2f5f9", "#ffffff", "#1d2a3a", "#b6c6d9", "#00766c", "#2459a0", "#d9eaff"),
    "dark-purple": ("#1b1526", "#282039", "#f1e9fb", "#594472", "#73e5bf", "#c6a5ff", "#4b3768"),
    "dark-blue": ("#101722", "#182436", "#e7eef8", "#40526c", "#65dfc1", "#8bb9ff", "#304962"),
    "dark-black": ("#101112", "#202224", "#f3f3f3", "#55585e", "#71e3c0", "#a6caff", "#3c4659"),
    "light-warm": ("#f6efe5", "#fff9ef", "#3d2b20", "#c8af93", "#386a4c", "#95542e", "#f3dec6"),
    "light-mint": ("#eaf6f0", "#f9fffb", "#153b32", "#9cbfb0", "#007861", "#315f92", "#d7e9f3"),
    "dark-teal": ("#0c1e21", "#132c31", "#e3f5f2", "#386063", "#7ce7c8", "#8ac7f4", "#264e59"),
    "dark-warm": ("#251b17", "#342720", "#ffedde", "#735542", "#9ed19a", "#ffbb86", "#573c33"),
    "dark-glass": ("#11182c", "#202b49", "#eef1ff", "#46577c", "#8bf0d9", "#a7b6ff", "#37486a"),
}


def colors_for(theme, scheme):
    if theme == "system":
        theme = "dark-blue" if scheme == Qt.ColorScheme.Dark else "light"
    return COLORS.get(theme, COLORS["dark-blue"])


def stylesheet(colors):
    background, surface, text, border, accent, _, _ = colors
    return f"""
        QWidget {{ background: {background}; color: {text}; font-size: 14px; }}
        QWidget[aboutCard="true"] {{ background: {surface}; border-radius: 8px; }}
        QWidget[aboutCard="true"] QLabel {{ background: transparent; }}
        QLabel#title {{ font-family: "Kalam", "Segoe UI"; font-size: 37px; font-weight: 700; }}
        QLabel[sectionTitle="true"] {{ font-size: 17px; font-weight: 650; }}
        QLabel#dataset {{ font-size: 16px; font-weight: 650; }}
        QLabel#metrics {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 10px; }}
        QLabel#quote {{ color: {accent}; font-weight: 650; }}
        QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
            background: {surface}; border: 1px solid {border}; border-radius: 6px; padding: 6px 10px;
        }}
        QMenu {{ background: {surface}; border: 1px solid {border}; padding: 4px; }}
        QMenu::item {{ padding: 6px 20px; }}
        QMenu::item:selected {{ background: {background}; color: {accent}; }}
        QPushButton:checked {{ border-color: {accent}; color: {accent}; }}
        QProgressBar {{ background: {surface}; border: 0; border-radius: 2px; }}
        QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}
        QPushButton[headerAction="true"] {{ padding: 5px 6px; }}
        QLabel#updateStatus {{ font-size: 12px; color: {accent}; }}
        QPushButton:hover, QPushButton:focus, QLineEdit:focus, QSpinBox:focus {{
            border-color: {accent};
        }}
    """
