"""Visual system: graphite ramp, one accent (amber unless the user picks another), lucide line icons.

Direction (see docs/design.md): an edit bay at night. The chrome stays
quiet, the stroke curve is the one element that carries colour.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSlider,
    QToolButton,
    QWidget,
)

from ..i18n import tr

# ------------------------------------------------------------ tokens
DEEP = "#0f0f10"       # toolbar, sidebar
BG = "#141415"         # page
CARD = "#19191b"       # video, curve
RAISED = "#232325"     # inputs, hover, active
HAIRLINE = "rgba(237, 237, 237, 0.08)"
TEXT = "#ededed"
TEXT_2 = "#a1a1a6"
MUTED = "#85858b"
ACCENT = "#e5a043"     # amber tally light; set_accent() changes the three accent tokens
ACCENT_HOVER = "#eeb25e"
ON_ACCENT = "#141415"
NEGATIVE = "#e5484d"

#: accent colours to choose from (key -> label, colour); any other colour can be picked too.
#: All bright enough for dark text on them and far enough from NEGATIVE (stop, errors).
ACCENTS = {
    "amber": (tr("Bernstein"), "#e5a043"),
    "blue": (tr("Blau"), "#5b9cf5"),
    "teal": (tr("Türkis"), "#3cc2b4"),
    "green": (tr("Grün"), "#7cc35a"),
    "violet": (tr("Violett"), "#a688f7"),
    "pink": (tr("Pink"), "#ec6fb0"),
}
DEFAULT_ACCENT = ACCENTS["amber"][1]


def _luminance(c: QColor) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def set_accent(color: str) -> str:
    """Make `color` the accent (invalid -> default). Text and icons on the accent turn
    white when the colour is too dark for dark text. Returns the colour used."""
    global ACCENT, ACCENT_HOVER, ON_ACCENT
    c = QColor(color)
    if not c.isValid():
        c = QColor(DEFAULT_ACCENT)
    c.setAlpha(255)
    ACCENT = c.name()
    dark_text = (_luminance(c) + 0.05) / (_luminance(QColor(BG)) + 0.05)
    light_text = 1.05 / (_luminance(c) + 0.05)
    ON_ACCENT = BG if dark_text >= light_text else "#ffffff"
    ACCENT_HOVER = (c.lighter(112) if ON_ACCENT == BG else c.darker(112)).name()
    return ACCENT


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
    "scissors": '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/>'
                '<circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
             '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    "library": '<path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/>',
    "bookmark": '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
    "waves": '<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>'
             '<path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>'
             '<path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>',
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
    "bluetooth": '<path d="m7 7 10 10-5 5V2l5 5L7 17"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "rotate-ccw": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "list-video": '<path d="M12 12H3"/><path d="M16 6H3"/><path d="M12 18H3"/><path d="m16 12 5 3-5 3v-6Z"/>',
    "skip-forward": '<path d="M5 4l10 8-10 8z"/><path d="M19 5v14"/>',
    "skip-back": '<path d="M19 20 9 12l10-8z"/><path d="M5 19V5"/>',
    "shuffle": '<path d="m18 14 4 4-4 4"/><path d="m18 2 4 4-4 4"/><path d="M2 18h1.973a4 4 0 0 0 3.3-1.7l5.454-8.6a4 4 0 0 1 3.3-1.7H22"/><path d="M2 6h1.972a4 4 0 0 1 3.6 2.2"/><path d="M22 18h-6.041a4 4 0 0 1-3.3-1.8l-.359-.45"/>',
    "repeat": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "maximize": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    "pencil": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/>',
    "crosshair": '<circle cx="12" cy="12" r="10"/><path d="M22 12h-4"/><path d="M6 12H2"/><path d="M12 6V2"/><path d="M12 22v-4"/>',
    "volume": '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>',
    "volume-x": '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M22 9l-6 6"/><path d="M16 9l6 6"/>',
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


_accent_icons: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()   # button -> icon name


def accent_icon(button, name: str, text: str | None = None):
    """Give a button on the accent (primary) an icon in the colour on the accent; it is
    redrawn when the accent changes. Returns the button."""
    _accent_icons[button] = name
    button.setIcon(icon(name, ON_ACCENT))
    if text is not None:
        button.setText(text)
    return button


def icon(name: str, color: str = TEXT_2, px: int = 16) -> QIcon:
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, color, px), QIcon.Normal)
    ic.addPixmap(icon_pixmap(name, TEXT, px), QIcon.Active)
    ic.addPixmap(icon_pixmap(name, qcolor(MUTED, 0.45).name(QColor.HexArgb), px), QIcon.Disabled)
    return ic


# ------------------------------------------------------------ application style
def build_qss() -> str:
    return f"""
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
QWidget#timelinePanel {{ background: {CARD}; }}
QLabel#laneLabel {{ color: {MUTED}; font-size: 11px; }}
QToolButton#reset {{ padding: 2px 6px; color: {MUTED}; }}
QToolButton#reset:hover {{ color: {TEXT}; }}
QToolButton#disclosure {{ padding: 4px 4px 4px 0; color: {TEXT_2}; }}
QToolButton#disclosure:checked {{ background: transparent; color: {TEXT}; }}
QLabel {{ background: transparent; }}
QLabel#section {{ color: {TEXT}; font-weight: 500; }}
QLabel#caption, QLabel#meta {{ color: {MUTED}; }}
QLabel#formLabel {{ color: {TEXT_2}; }}
QLabel#value {{ color: {TEXT_2}; }}

QPushButton {{ background: {RAISED}; border: 1px solid {HAIRLINE}; border-radius: 8px;
              padding: 0 16px; min-height: 32px; color: {TEXT}; }}
QPushButton:hover {{ background: #2a2a2d; }}
QPushButton:checked {{ color: {ACCENT}; border-color: {ACCENT}; }}
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
QPushButton#tool {{ background: transparent; border: 1px solid {HAIRLINE}; min-height: 28px; padding: 0 12px;
                   border-radius: 8px; color: {TEXT_2}; }}
QPushButton#tool:hover {{ background: {RAISED}; color: {TEXT}; }}
QPushButton#tool:checked {{ background: {RAISED}; color: {ACCENT}; border-color: {ACCENT}; }}

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
QSlider#volume {{ min-width: 72px; max-width: 88px; }}

QTreeWidget#library {{ background: {DEEP}; border: 1px solid {HAIRLINE}; border-radius: 8px; outline: none; }}
QTreeWidget#library::item {{ padding: 8px 4px; color: {TEXT}; }}
QTreeWidget#library::item:hover {{ background: {CARD}; }}
QTreeWidget#library::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QListWidget#playlist {{ background: {DEEP}; border: none; outline: none; padding: 0 8px; }}
QListWidget#playlist::item {{ padding: 8px; border-radius: 8px; color: {TEXT_2}; margin: 1px 0; }}
QListWidget#playlist::item:hover {{ background: {CARD}; }}
QListWidget#playlist::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QListWidget#settingsNav {{ background: {DEEP}; border: none; outline: none; padding: 16px 8px; }}
QListWidget#settingsNav::item {{ padding: 8px 12px; border-radius: 8px; color: {TEXT_2}; margin: 1px 0; }}
QListWidget#settingsNav::item:hover {{ background: {CARD}; }}
QListWidget#settingsNav::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QToolButton:checked {{ background: {RAISED}; color: {ACCENT}; }}
QPushButton#transport:checked {{ background: {RAISED}; }}
QPushButton#segment {{ background: transparent; border: none; min-height: 28px; padding: 0 12px;
    color: {TEXT_2}; border-radius: 6px; }}
QPushButton#segment:hover {{ color: {TEXT}; }}
QPushButton#segment:checked {{ background: {RAISED}; color: {TEXT}; }}

QProgressBar {{ background: {RAISED}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {RAISED}; border-radius: 4px; min-height: 24px; }}
QScrollBar:horizontal {{ background: {RAISED}; height: 10px; margin: 0; border-radius: 5px; }}
QScrollBar::handle:horizontal {{ background: {MUTED}; border-radius: 5px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {TEXT_2}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {DEEP}; border-top: 1px solid {HAIRLINE}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 0 8px; }}

QPlainTextEdit {{ background: {CARD}; border: 1px solid {HAIRLINE}; border-radius: 8px; padding: 8px; color: {TEXT_2}; }}
QDialog, QMessageBox {{ background: {BG}; }}
"""


def apply(app: QApplication) -> None:
    """Style the whole app with the current tokens; called again when the accent changes."""
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
    app.setStyleSheet(build_qss().replace("%CHECK%", _icon_file("check", ON_ACCENT))
                         .replace("%CHEVRON%", _icon_file("chevron-down", TEXT_2)))
    if not hasattr(app, "_vtf_fit"):
        app._vtf_fit = DialogFitter(app)
        app.installEventFilter(app._vtf_fit)


def change_accent(app: QApplication, color: str) -> str:
    """Switch the accent while the app runs: style, palette, accent icons, repaint."""
    used = set_accent(color)
    apply(app)
    for button, name in list(_accent_icons.items()):
        try:
            button.setIcon(icon(name, ON_ACCENT))
        except RuntimeError:          # widget already deleted
            pass
    for w in app.allWidgets():
        w.update()
    return used


# ------------------------------------------------------------ reset to defaults
def reset_button(on_click, tip: str | None = None) -> QToolButton:
    b = QToolButton()
    b.setObjectName("reset")
    b.setText(tr("Standard"))
    b.setIcon(icon("rotate-ccw", MUTED, 14))
    b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    b.setToolTip(tip or tr("Alles in diesem Bereich auf Standard zurücksetzen"))
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(lambda *_: on_click())
    return b


def section_title(text: str, on_reset=None, tip: str | None = None) -> QWidget:
    """A section heading, with a "Standard" button on the right when on_reset is given."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    t = QLabel(text)
    t.setObjectName("section")
    lay.addWidget(t)
    lay.addStretch(1)
    if on_reset is not None:
        w.reset_btn = reset_button(on_reset, tip) if tip else reset_button(on_reset)
        lay.addWidget(w.reset_btn)
    return w


class _SliderReset(QObject):
    def __init__(self, slider: QSlider, default: int):
        super().__init__(slider)
        self.default = default

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonDblClick:
            obj.setValue(self.default)
            return True
        return False


def double_click_reset(slider: QSlider, default: int) -> None:
    """Double-click on the slider sets it back to its default."""
    f = _SliderReset(slider, int(default))
    slider.installEventFilter(f)
    slider._reset_filter = f
    tip = slider.toolTip()
    slider.setToolTip((tip + "\n" if tip else "") + tr("Doppelklick = Standard"))


class DialogFitter(QObject):
    """Keeps every dialog at least as tall as its content really needs.

    Qt's minimum size ignores wrapped text (height-for-width), so with larger fonts or another window
    manager a dialog could be shown or shrunk too small and squeeze its rows. After each show, resize
    or layout change the minimum height is set to what the layout needs at the current width.
    Applies to dialogs whose layout uses SetMinimumSize (the form-like dialogs); big resizable
    windows like the pattern control keep their own sizing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: set[int] = set()

    def eventFilter(self, obj, ev):
        if (ev.type() in (QEvent.Show, QEvent.Resize, QEvent.LayoutRequest) and isinstance(obj, QDialog)
                and id(obj) not in self._pending):
            key = id(obj)
            self._pending.add(key)
            QTimer.singleShot(0, lambda d=obj, k=key: self._fit(d, k))
        return False

    def _fit(self, dlg, key) -> None:
        self._pending.discard(key)
        try:
            lay = dlg.layout()
            if lay is None or not dlg.isVisible() or lay.sizeConstraint() != QLayout.SetMinimumSize:
                return
            lay.activate()
            m = dlg.contentsMargins()
            w = dlg.width() - m.left() - m.right()
            need = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalMinimumSize().height()
            need += m.top() + m.bottom()
            screen = dlg.screen()
            if screen is not None:
                need = min(need, screen.availableGeometry().height() - 40)
            if need > dlg.minimumHeight():
                dlg.setMinimumHeight(need)
            if dlg.height() < need:
                dlg.resize(dlg.width(), need)
        except RuntimeError:          # dialog already deleted
            pass


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
