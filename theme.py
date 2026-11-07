"""Light theme: white cards on soft gray, monochrome selection, red reserved for errors."""

BG = '#f4f5f7'
CARD = '#ffffff'
SOFT = '#f9fafb'
BORDER = '#e5e7eb'
STRONG = '#111827'
TEXT = '#0f172a'
BODY = '#374151'
MUTED = '#6b7280'
FAINT = '#9ca3af'
OK = '#16a34a'
BAD = '#dc2626'
BAD_BG = '#fef2f2'
BAD_BORDER = '#fecaca'
WARN = '#b45309'
MONO = 'Menlo'

APP_QSS = f"""
* {{ font-family: 'PingFang SC', 'Microsoft YaHei'; }}
QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QLabel {{ background: transparent; }}

QFrame[card="true"] {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px;
}}
QLabel[h1="true"] {{ font-size: 22px; font-weight: 800; letter-spacing: -0.3px; }}
QLabel[sub="true"] {{ color: {MUTED}; font-size: 12.5px; }}
QLabel[cardTitle="true"] {{ font-size: 15px; font-weight: 700; }}
QLabel[hint="true"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[faint="true"] {{ color: {FAINT}; font-size: 12px; }}
QLabel#logo {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px;
    font-size: 22px; padding: 4px 9px;
}}

QLineEdit {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 9px 12px; color: {TEXT}; font-size: 13.5px;
    selection-background-color: #dbeafe; selection-color: {TEXT};
}}
QLineEdit:focus {{ border: 1px solid {STRONG}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {BAD}; background: {BAD_BG}; }}
QLineEdit:disabled {{ color: {FAINT}; background: {SOFT}; }}

QPushButton {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 8px 16px; color: {TEXT}; font-size: 13px;
}}
QPushButton:hover {{ background: {SOFT}; border-color: #d1d5db; }}
QPushButton:pressed {{ background: #f3f4f6; }}
QPushButton:disabled {{ color: {FAINT}; background: {SOFT}; border-color: {BORDER}; }}
QPushButton#primary {{
    background: {STRONG}; color: #ffffff; border: none;
    padding: 11px 28px; border-radius: 10px; font-size: 14px; font-weight: 700;
}}
QPushButton#primary:hover {{ background: #1f2937; }}
QPushButton#primary:disabled {{ background: #d1d5db; color: #ffffff; }}

QFrame[fileRow="true"] {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 11px;
}}
QFrame[errorPanel="true"] {{
    background: {BAD_BG}; border: 1px solid {BAD_BORDER}; border-radius: 11px;
}}
QLabel[errTitle="true"] {{ color: {BAD}; font-weight: 700; font-size: 13.5px; }}
QLabel[errLine="true"] {{ color: {BODY}; font-size: 12.5px; }}
QLabel[errNote="true"] {{ color: {WARN}; font-size: 12.5px; }}

QFrame[featCard="true"] {{
    background: {SOFT}; border: 1.5px solid {BORDER}; border-radius: 11px;
}}
QFrame[featCard="true"]:hover {{ border-color: #d1d5db; }}
QFrame[featCard="true"][selected="true"] {{
    background: {CARD}; border: 1.5px solid {STRONG};
}}
QFrame[featCard="true"]:disabled {{ background: {SOFT}; }}
QLabel[featTitle="true"] {{ font-size: 14px; font-weight: 700; color: {TEXT}; }}
QLabel[featDesc="true"] {{ font-size: 12.5px; color: {MUTED}; }}

QTextEdit {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px;
    font-family: {MONO}; font-size: 12px; padding: 8px 10px;
    selection-background-color: #dbeafe; selection-color: {TEXT};
}}
QProgressBar {{
    background: #e9ebef; border: none; border-radius: 3px; max-height: 6px;
}}
QProgressBar::chunk {{ background: {STRONG}; border-radius: 3px; }}
"""


def chip(label, kind):
    """Small status pill: kind in ok / bad / idle."""
    colors = {
        'ok': (OK, '#f0fdf4', '#bbf7d0'),
        'bad': (BAD, BAD_BG, BAD_BORDER),
        'idle': (MUTED, SOFT, BORDER),
    }
    fg, bg, bd = colors.get(kind, colors['idle'])
    label.setStyleSheet(
        f'color: {fg}; background: {bg}; border: 1px solid {bd};'
        f'border-radius: 999px; padding: 4px 12px; font-size: 12px; font-weight: 600;')
