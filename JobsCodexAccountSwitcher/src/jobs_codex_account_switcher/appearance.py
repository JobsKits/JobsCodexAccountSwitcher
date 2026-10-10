"""账户窗口与 Token 控件共用三态配色。Created by Jobs."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_appearance(mode):
    app = QApplication.instance()
    dark = mode == 2 or (mode == 0 and app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    palette = QPalette()
    roles = QPalette.ColorRole
    colors = {
        roles.Window: '#202124' if dark else '#f5f6f8',
        roles.WindowText: '#f1f3f4' if dark else '#202124',
        roles.Base: '#292b2f' if dark else '#ffffff',
        roles.AlternateBase: '#34373d' if dark else '#eef1f5',
        roles.Text: '#f1f3f4' if dark else '#202124',
        roles.Button: '#34373d' if dark else '#e7eaf0',
        roles.ButtonText: '#f1f3f4' if dark else '#202124',
        roles.Highlight: '#3977bc', roles.HighlightedText: '#ffffff',
        roles.ToolTipBase: '#292b2f' if dark else '#ffffff',
        roles.ToolTipText: '#f1f3f4' if dark else '#202124',
        roles.PlaceholderText: '#aab0b8' if dark else '#626975',
        roles.Light: '#565b65' if dark else '#ffffff',
        roles.Mid: '#41454d' if dark else '#bac1cc',
        roles.Dark: '#151619' if dark else '#8b929e',
        roles.Shadow: '#111111' if dark else '#646a74',
        roles.Link: '#91c3ff' if dark else '#155ca8',
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (roles.Text, roles.WindowText, roles.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor('#969aa4' if dark else '#727987'))
    app.setPalette(palette)

