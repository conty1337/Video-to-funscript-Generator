"""Visual system: graphite ramp, one amber accent, lucide line icons.

Direction (see docs/design.md): an edit bay at night. The chrome stays
quiet, the stroke curve is the one element that carries colour.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

# ------------------------------------------------------------ tokens
DEEP = "#0f0f10"       # toolbar, sidebar
BG = "#141415"         # page
CARD = "#19191b"       # video, curve
RAISED = "#232325"     # inputs, hover, active
HAIRLINE = "rgba(237, 237, 237, 0.08)"
TEXT = "#ededed"
TEXT_2 = "#a1a1a6"
MUTED = "#85858b"
ACCENT = "#e5a043"     # amber tally light
ACCENT_HOVER = "#eeb25e"
ON_ACCENT = "#141415"
NEGATIVE = "#e5484d"


def qcolor(hex_or_rgba: str, alpha: float | None = None) -> QColor:
    c = QColor(hex_or_rgba)
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


def mono_font(px: int = 12) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPixelSize(px)
    f.setStyleHint(QFont.Monospace)
    return f


# ------------------------------------------------------------ icons
# lucide icons (ISC licence), 24x24 viewBox, stroke 1.5
_ICONS = {
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "pause": '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 '
                   '1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 '
                   '0 0 1 2 2v2"/>',
    "save": '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 '
            '2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
    "folders": '<path d="M20 17a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.9a2 2 0 0 1-1.69-.9l-.81-1.2a2 2 0 0 '
               '0-1.67-.9H8a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2Z"/><path d="M2 8v11a2 2 0 0 0 2 2h14"/>',
    "activity": '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 '
                '0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "film": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/>'
            '<path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/>'
            '<path d="M17 16.5h4"/>',
}


def icon_pixmap(name: str, color: str = TEXT_2, px: int = 16, ratio: float = 2.0) -> QPixmap:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
           f'{_ICONS[name]}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(int(px * ratio), int(px * ratio))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    renderer.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str = TEXT_2, px: int = 16) -> QIcon:
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, color, px), QIcon.Normal)
    ic.addPixmap(icon_pixmap(name, TEXT, px), QIcon.Active)
    ic.addPixmap(icon_pixmap(name, qcolor(MUTED, 0.45).name(QColor.HexArgb), px), QIcon.Disabled)
    return ic


# ------------------------------------------------------------ application style
QSS = f"""
* {{ font-size: 13px; }}
QWidget {{ color: {TEXT}; background: {BG}; }}
QMainWindow::separator, QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:horizontal {{ width: 1px; background: {HAIRLINE}; }}
QSplitter::handle:vertical {{ height: 1px; background: {HAIRLINE}; }}

QToolTip {{ color: {TEXT}; background: {RAISED}; border: 1px solid {HAIRLINE}; padding: 4px 8px; }}

QMenuBar {{ background: {DEEP}; padding: 0 4px; }}
QMenuBar::item {{ background: transparent; padding: 4px 8px; border-radius: 6px; color: {TEXT_2}; }}
QMenuBar::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QMenu {{ background: {CARD}; border: 1px solid {HAIRLINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; background: transparent; }}
QMenu::item:selected {{ background: {RAISED}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {HAIRLINE}; margin: 4px 8px; }}

QToolBar {{ background: {DEEP}; border: none; border-bottom: 1px solid {HAIRLINE}; padding: 4px 12px; spacing: 4px; }}
QToolBar QWidget {{ background: transparent; }}
QToolBar::separator {{ width: 1px; background: {HAIRLINE}; margin: 6px 8px; }}
QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 6px 10px; color: {TEXT_2}; }}
QToolButton:hover {{ background: {RAISED}; color: {TEXT}; }}
QToolButton:pressed {{ background: {CARD}; }}
QToolButton:disabled {{ color: {MUTED}; }}

QWidget#sidebar, QScrollArea#sidebarScroll, QWidget#sidebarFooter {{ background: {DEEP}; }}
QScrollArea {{ border: none; }}
QLabel {{ background: transparent; }}
QLabel#section {{ color: {TEXT}; font-weight: 500; }}
QLabel#caption, QLabel#meta {{ color: {MUTED}; }}
QLabel#formLabel {{ color: {TEXT_2}; }}
QLabel#value {{ color: {TEXT_2}; }}

QPushButton {{ background: {RAISED}; border: 1px solid {HAIRLINE}; border-radius: 8px;
              padding: 0 16px; min-height: 32px; color: {TEXT}; }}
QPushButton:hover {{ background: #2a2a2d; }}
QPushButton:pressed {{ background: {CARD}; }}
QPushButton:disabled {{ color: {MUTED}; background: {CARD}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ON_ACCENT}; border: none; font-weight: 500; min-height: 36px; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: {RAISED}; color: {MUTED}; }}
QPushButton#statusAction {{ background: transparent; border: none; min-height: 20px; padding: 0 8px;
                           color: {ACCENT}; }}
QPushButton#statusAction:hover {{ color: {ACCENT_HOVER}; text-decoration: underline; }}
QPushButton#statusAction:disabled {{ color: {TEXT_2}; background: transparent; }}
QPushButton#transport {{ background: transparent; border: none; min-height: 32px; min-width: 32px;
                        max-width: 32px; padding: 0; border-radius: 8px; }}
QPushButton#transport:hover {{ background: {RAISED}; }}

QComboBox, QSpinBox, QLineEdit {{ background: {RAISED}; border: 1px solid {HAIRLINE}; border-radius: 8px;
                                  padding: 0 8px; min-height: 28px; selection-background-color: {ACCENT};
                                  selection-color: {ON_ACCENT}; }}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: rgba(237, 237, 237, 0.16); }}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QSpinBox QLineEdit, QAbstractSpinBox QLineEdit {{ background: transparent; border: none; padding: 0;
                                                 min-height: 0; border-radius: 0; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 0px; height: 0px;
                                              border: none; image: none; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url(%CHEVRON%); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {HAIRLINE}; padding: 4px;
                               selection-background-color: {RAISED}; selection-color: {TEXT}; outline: none; }}

QCheckBox {{ spacing: 8px; color: {TEXT_2}; background: transparent; }}
QCheckBox:hover {{ color: {TEXT}; }}
QCheckBox:disabled {{ color: {MUTED}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px; background: {RAISED};
                       border: 1px solid rgba(237, 237, 237, 0.16); }}
QCheckBox::indicator:hover {{ border-color: rgba(237, 237, 237, 0.32); }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(%CHECK%); }}
QCheckBox::indicator:disabled {{ background: {CARD}; border-color: {HAIRLINE}; }}

QSlider {{ background: transparent; min-height: 20px; }}
QSlider::groove:horizontal {{ height: 4px; background: {RAISED}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {TEXT_2}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::sub-page:horizontal:disabled {{ background: {RAISED}; }}
QSlider::handle:horizontal:disabled {{ background: {MUTED}; }}
QSlider#timeline::sub-page:horizontal {{ background: {ACCENT}; }}

QProgressBar {{ background: {RAISED}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {RAISED}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {DEEP}; border-top: 1px solid {HAIRLINE}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 0 8px; }}

QPlainTextEdit {{ background: {CARD}; border: 1px solid {HAIRLINE}; border-radius: 8px; padding: 8px; color: {TEXT_2}; }}
QDialog, QMessageBox {{ background: {BG}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in (
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, RAISED),
        (QPalette.AlternateBase, CARD), (QPalette.Text, TEXT), (QPalette.Button, RAISED),
        (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, RAISED), (QPalette.ToolTipText, TEXT),
        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, ON_ACCENT),
        (QPalette.PlaceholderText, MUTED), (QPalette.Link, ACCENT), (QPalette.BrightText, NEGATIVE),
    ):
        pal.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(MUTED))
    pal.setColor(QPalette.Light, QColor(RAISED))
    pal.setColor(QPalette.Midlight, QColor(RAISED))
    pal.setColor(QPalette.Mid, QColor(CARD))
    pal.setColor(QPalette.Dark, QColor(DEEP))
    pal.setColor(QPalette.Shadow, QColor("#000000"))
    app.setPalette(pal)
    app.setStyleSheet(QSS.replace("%CHECK%", _icon_file("check", ON_ACCENT))
                         .replace("%CHEVRON%", _icon_file("chevron-down", TEXT_2)))


def _icon_file(name: str, color: str) -> str:
    """Qt style sheets need files for indicator images; render them once into the cache."""
    import os
    from pathlib import Path

    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "video-to-funscript" / "icons"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}-{color.lstrip('#')}.png"
    if not path.is_file():
        icon_pixmap(name, color, 14, 2.0).save(str(path))
    return path.as_posix()
