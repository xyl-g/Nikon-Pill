"""Chinese desktop debugger for the Fenestra Z8 C3.11 development port."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import sys
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk

from z8_debug_session import DebugWorker

# Graphite surfaces with one warm signal colour, like a viewfinder overlay.
BG = '#0f1114'
RAIL = '#13161a'
SURFACE = '#181b20'
RAISED = '#22262d'
HOVER = '#2d323a'
INPUT = '#0b0d10'
LINE = '#262b33'
TEXT = '#ece9e2'
MUTED = '#8d929b'
FAINT = '#5f656f'
ACCENT = '#f0c25e'
ACCENT_HOVER = '#f7d68e'
ON_ACCENT = '#1a1508'
OK = '#6fd49b'
WARN = '#ffab5c'
ERROR = '#ff7b6e'
INFO = '#8ab4ff'
OUTPUT = '#d6e4da'
NAV_HOVER = '#191c21'
SELECT = '#3b414b'
ALERT_BG = '#2a1815'
ALERT_LINE = '#5c2b25'
NOTE_BG = '#221d12'
NOTE_LINE = '#4a3d1d'
DONE_BG = '#1d3527'
FONT = 'Microsoft YaHei UI'

# The constants above are the dark palette; DebugApp swaps them for the active
# theme. Every role keeps a distinct value so live widgets can be remapped.
ROLES = ('BG', 'RAIL', 'SURFACE', 'RAISED', 'HOVER', 'INPUT', 'LINE', 'TEXT', 'MUTED',
         'FAINT', 'ACCENT', 'ACCENT_HOVER', 'ON_ACCENT', 'OK', 'WARN', 'ERROR', 'INFO',
         'OUTPUT', 'NAV_HOVER', 'SELECT', 'ALERT_BG', 'ALERT_LINE', 'NOTE_BG', 'NOTE_LINE',
         'DONE_BG')
PALETTES = {
    'dark': {role: globals()[role] for role in ROLES},
    # Warm paper with deepened signal colours, so text stays legible on white.
    'light': dict(BG='#f6f5f2', RAIL='#efeee9', SURFACE='#ffffff', RAISED='#e6e4de',
                  HOVER='#d9d6cf', INPUT='#fdfdfb', LINE='#d8d5ce', TEXT='#1d1f23',
                  MUTED='#5f646d', FAINT='#9a9ea6', ACCENT='#a86f00', ACCENT_HOVER='#c08515',
                  ON_ACCENT='#ffffff', OK='#1f8a52', WARN='#b85c00', ERROR='#c8372d',
                  INFO='#2f62c9', OUTPUT='#21452f', NAV_HOVER='#e9e7e1', SELECT='#cfe0f5',
                  ALERT_BG='#fdecea', ALERT_LINE='#f0b8b2', NOTE_BG='#fbf3df',
                  NOTE_LINE='#e8d3a0', DONE_BG='#dcefe3'),
}
COLOR_OPTIONS = ('background', 'foreground', 'activebackground', 'activeforeground',
                 'highlightbackground', 'highlightcolor', 'insertbackground', 'selectbackground',
                 'selectforeground', 'disabledforeground', 'disabledbackground', 'selectcolor')
SETTINGS = Path(os.environ.get('APPDATA') or Path.home()) / 'Fenestra' / 'z8-debug-ui.json'

ACTION_LABELS = {
    'connect': '连接相机', 'disconnect': '断开连接', 'refresh': '刷新状态',
    'echo': '连接测试', 'execute': '执行 RTOS 命令', 'native_call': '调用原厂函数', 'memory_write': '写入调试内存', 'recover': '恢复结果',
    'rtos_status': '查询任务', 'registers': '读取寄存器',
    'memory_read': '读取内存', 'memory_dump': '导出内存', 'card_upload': '安装卡上 WLM', 'card_resolve': '收尾卡上传',
    'card_probe': '检查卡上文件',
    'camera_control': '相机拍摄控制',
    'linux_status': '读取 Linux 桥状态', 'linux_start': '启动 Linux 代理',
    'linux_stop': '停止 Linux 代理', 'linux_execute': '执行 Linux 命令',
    'linux_download': '下载文件', 'linux_upload': '上传文件',
}

PAGES = [
    ('terminal', '>_', 'RTOS 控制台', '命令 · 任务 · 日志'),
    ('memory', '0x', '寄存器与内存', '系统寄存器 · 十六进制'),
    ('linux', '$_', 'Linux 代理', 'RAM 代理 · 命令 · 文件'),
    ('calls', '↔', '调用记录', '界面 · 自动化 · 结果'),
]

REGISTERS = ('CurrentEL', 'DAIF', 'TTBR0_EL1', 'TTBR1_EL1', 'TCR_EL1', 'MAIR_EL1',
             'VBAR_EL1', 'TPIDRRO_EL0', 'CNTPCT_EL0', 'CNTFRQ_EL0')

JOB_STATES = {'idle': ('空闲', 'MUTED'), 'queued': ('排队', 'INFO'), 'running': ('运行中', 'INFO'),
              'done': ('完成', 'OK'), 'expired': ('已过期', 'WARN'), 'revoked': ('已撤销', 'WARN')}

CANCELLABLE = ('execute', 'native_call', 'card_upload', 'recover', 'linux_execute')


class DebugApp:
    def __init__(self, window, worker=None, theme='dark', automation=False, bridge_directory=None):
        self.window = window
        self.theme = theme
        globals().update(PALETTES[theme])
        self.buttons = []
        self.worker = worker or DebugWorker()
        self.bridge = None
        self.remote_request = None
        self.busy = self.closing = self.destroyed = self.trouble = False
        self.active_action = None
        self.close_done = False
        self.state = dict(connected=False, report=None, pending=False, rtos_job=None,
                          linux_ready=False, linux_job=None, linux_owned=False, linux_pending=False)
        self.journal = []
        self.controls = []
        self.pages, self.nav_items = {}, {}
        self.current_page = 'terminal'
        self.poll_id = self.auto_id = self.spin_id = None
        self.spin_angle, self.spin_key = 0, None
        self.history, self.history_at = {}, {}
        self.last_seen = None
        self.alert_shown = False
        # Pixel geometry follows the monitor DPI; font point sizes scale by themselves.
        self.scale = max(1.0, window.winfo_fpixels('1i') / 96)
        families = set(tkfont.families(window))
        self.mono = next((f for f in ('Cascadia Mono', 'Consolas') if f in families), 'Courier New')
        self.auto = tk.BooleanVar(value=False)
        self.command = tk.StringVar(value='echo Fenestra')
        self.timeout = tk.StringVar(value='5000')
        self.address = tk.StringVar(value='0x40000000')
        self.count = tk.StringVar(value='256')
        self.agent = tk.StringVar(value='')
        self.linux_command = tk.StringVar(value='uname -a')
        self.remote = tk.StringVar(value='/tmp/')
        self.status = tk.StringVar(value='就绪 · 点击“连接 Z8”开始；打开窗口不会访问相机。')
        self.linux_summary = tk.StringVar(value='代理尚未启动')
        self._layout()
        self._update_controls()
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Control-Return>', lambda event: self._execute())
        self.window.bind('<Control-s>', lambda event: self.save_log())
        self.window.bind('<Control-l>', lambda event: self.clear_view())
        self.window.bind('<Control-t>', lambda event: self.toggle_theme())
        for number, (key, *_) in enumerate(PAGES, 1):
            self.window.bind(f'<Control-Key-{number}>', lambda event, k=key: self.show_page(k))
        self._log('本地', '工作台已启动。先在机内 Activate，并打开 USB Debug，再连接 USB 数据线。')
        self._activity('工作台已启动', '打开窗口不会访问相机', 'muted')
        if automation:
            try:
                from workbench_bridge import WorkbenchBridge
                self.bridge = WorkbenchBridge(bridge_directory)
                self.bridge_info.configure(text='本地调用入口已开启 · ' + self.bridge.url)
                self._log('工作台', '本地调用入口已开启；调用过程见“调用记录”。')
                self._call_log('工作台', '会话审计日志：' + str(self.bridge.audit_path))
            except Exception as exc:
                self.bridge_info.configure(text='本地调用入口启动失败')
                self._local_error('本地调用入口启动失败：' + str(exc))
        self.poll_id = window.after(40, self._drain)
        self.auto_id = window.after(2000, self._auto_refresh)
        self.spin_id = window.after(70, self._spin)

    # ---- widget kit -------------------------------------------------------

    def px(self, value):
        return round(value * self.scale)

    def _label(self, parent, text='', color=TEXT, size=10, bold=False, bg=None, mono=False, **kwargs):
        return tk.Label(parent, text=text, bg=bg or parent.cget('bg'), fg=color,
                        font=(self.mono if mono else FONT, size, 'bold' if bold else 'normal'), **kwargs)

    def _button(self, parent, text, command, kind='secondary', requirement=None, small=False):
        b = tk.Button(parent, text=text, command=command, relief='flat', bd=0, overrelief='flat',
                      highlightthickness=0, cursor='hand2', disabledforeground=FAINT,
                      padx=self.px(10 if small else 15), pady=self.px(4 if small else 7))
        b.kind, b.size, b.hovered = kind, 9 if small else 10, False
        self.buttons.append(b)
        b.bind('<Enter>', lambda event: self._hover(b, True))
        b.bind('<Leave>', lambda event: self._hover(b, False))
        self._paint(b)
        if requirement:
            self.controls.append((b, requirement))
        return b

    def _hover(self, button, inside):
        button.hovered = inside
        self._paint(button)

    def _paint(self, button):
        enabled = str(button.cget('state')) != 'disabled'
        if button.kind == 'primary' and enabled:
            base, lit, fg = ACCENT, ACCENT_HOVER, ON_ACCENT
        elif button.kind == 'ghost':
            base, lit, fg = button.master.cget('bg'), RAISED, MUTED if enabled else FAINT
        else:
            base, lit, fg = RAISED, HOVER, TEXT
        bg = lit if button.hovered and enabled else base
        button.configure(bg=bg, fg=fg, activebackground=lit, activeforeground=fg,
                         font=(FONT, button.size, 'bold' if button.kind == 'primary' else 'normal'))

    def _enable(self, button, enabled):
        button.configure(state='normal' if enabled else 'disabled')
        self._paint(button)

    def _entry(self, parent, variable, width=24, bare=False):
        return tk.Entry(parent, textvariable=variable, width=width, fg=TEXT,
                        bg=parent.cget('bg') if bare else INPUT, insertbackground=ACCENT,
                        relief='flat', font=(self.mono, 10), bd=self.px(2 if bare else 6),
                        highlightthickness=0 if bare else 1, highlightbackground=LINE,
                        highlightcolor=ACCENT, selectbackground=SELECT, selectforeground=TEXT)

    def _text(self, parent, height=10):
        frame = tk.Frame(parent, bg=INPUT, highlightthickness=1, highlightbackground=LINE)
        text = tk.Text(frame, bg=INPUT, fg=TEXT, insertbackground=ACCENT, relief='flat', bd=0,
                       font=(self.mono, 10), padx=self.px(16), pady=self.px(12), wrap='word',
                       height=height, state='disabled', selectbackground=SELECT, undo=False,
                       spacing1=self.px(1), spacing3=self.px(1))
        scroll = ttk.Scrollbar(frame, command=text.yview, style='Z.Vertical.TScrollbar')
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y', padx=(0, self.px(2)), pady=self.px(2))
        text.pack(side='left', fill='both', expand=True)
        for tag, color in (('muted', MUTED), ('accent', ACCENT), ('error', ERROR), ('amber', WARN),
                           ('ok', OK), ('info', INFO), ('output', OUTPUT), ('time', FAINT),
                           ('source', MUTED)):
            text.tag_configure(tag, foreground=color)
        text.tag_configure('time', font=(self.mono, 9))
        return frame, text

    def _caption(self, parent, text):
        return self._label(parent, text, color=FAINT, size=8, bold=True)

    def _rule(self, parent, **pack):
        tk.Frame(parent, bg=LINE, height=1).pack(fill='x', **pack)

    def _hint(self, parent, text, **kwargs):
        return self._label(parent, text, color=FAINT, size=8, **kwargs)

    @staticmethod
    def _append(widget, text, tag=None):
        widget.configure(state='normal')
        widget.insert('end', text, tag or '')
        # Bound the on-screen transcript; exported journal retains the full session.
        if int(widget.index('end-1c').split('.')[0]) > 5000:
            widget.delete('1.0', '1001.0')
        widget.see('end')
        widget.configure(state='disabled')

    @staticmethod
    def _clear(widget):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.configure(state='disabled')

    # ---- frame ------------------------------------------------------------

    def _layout(self):
        w, P = self.window, self.px
        w.title('Fenestra · Z8 调试工作台')
        width = min(P(1400), w.winfo_screenwidth() - P(60))
        height = min(P(900), w.winfo_screenheight() - P(90))
        w.geometry(f'{width}x{height}+{P(40)}+{P(30)}')
        w.minsize(min(P(1120), width), min(P(720), height))
        w.configure(bg=BG)
        self._style()
        self._titlebar()

        self._topbar(w)
        self._statusbar(w)
        body = tk.Frame(w, bg=BG)
        body.pack(fill='both', expand=True)
        self._rail(body)
        tk.Frame(body, bg=LINE, width=1).pack(side='left', fill='y')
        self._inspector(body)
        tk.Frame(body, bg=LINE, width=1).pack(side='right', fill='y')
        main = tk.Frame(body, bg=BG)
        main.pack(side='left', fill='both', expand=True)

        self.alert = tk.Frame(main, bg=ALERT_BG, highlightthickness=1, highlightbackground=ALERT_LINE)
        self.alert_text = self._label(self.alert, '', color=ERROR, size=9, anchor='w',
                                      justify='left', wraplength=P(720))
        self.alert_text.pack(side='left', fill='x', expand=True, padx=(P(14), P(8)), pady=P(8))
        self._button(self.alert, '关闭', self._hide_alert, kind='ghost', small=True).pack(side='right', padx=P(6))

        self.page_area = tk.Frame(main, bg=BG)
        self.page_area.pack(fill='both', expand=True, padx=P(28), pady=(P(22), P(18)))
        for key, *_ in PAGES:
            self.pages[key] = tk.Frame(self.page_area, bg=BG)
        self._terminal_page(self.pages['terminal'])
        self._memory_page(self.pages['memory'])
        self._linux_page(self.pages['linux'])
        self._calls_page(self.pages['calls'])
        self.show_page('terminal')

    def _style(self):
        style = ttk.Style(self.window)
        style.theme_use('clam')
        style.layout('Z.Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {
            'sticky': 'ns', 'children': [('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        style.configure('Z.Vertical.TScrollbar', background=RAISED, troughcolor=INPUT,
                        bordercolor=INPUT, lightcolor=RAISED, darkcolor=RAISED,
                        gripcount=0, arrowsize=self.px(9))
        style.map('Z.Vertical.TScrollbar', background=[('active', HOVER)])
        style.configure('Z.Horizontal.TProgressbar', background=ACCENT, troughcolor=INPUT,
                        bordercolor=LINE, lightcolor=ACCENT, darkcolor=ACCENT,
                        thickness=self.px(6))

    def _titlebar(self):
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            self.window.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.window.winfo_id())
            value = ctypes.c_int(self.theme == 'dark')
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, pre-20H1 value
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
        except Exception:
            pass

    # ---- theme ------------------------------------------------------------

    def _theme_label(self):
        return '浅色模式' if self.theme == 'dark' else '深色模式'

    def toggle_theme(self):
        self.apply_theme('light' if self.theme == 'dark' else 'dark')
        save_theme(self.theme)

    def apply_theme(self, name):
        if name == self.theme:
            return
        swap = {old.lower(): PALETTES[name][role] for role, old in PALETTES[self.theme].items()}
        globals().update(PALETTES[name])
        self.theme = name
        self._recolor(self.window, swap)
        self._style()
        self._titlebar()
        self.theme_button.configure(text=self._theme_label())
        for button in self.buttons:
            self._paint(button)
        for key in self.nav_items:
            self._nav_paint(key)
        self.spin_key = None
        self._update_controls()

    def _recolor(self, widget, swap):
        if not isinstance(widget, ttk.Widget):
            options = widget.keys()
            changes = {option: swap[str(widget.cget(option)).lower()] for option in COLOR_OPTIONS
                       if option in options and str(widget.cget(option)).lower() in swap}
            if changes:
                widget.configure(**changes)
        if isinstance(widget, tk.Text):
            for tag in widget.tag_names():
                for option in ('foreground', 'background'):
                    value = str(widget.tag_cget(tag, option)).lower()
                    if value in swap:
                        widget.tag_configure(tag, **{option: swap[value]})
        elif isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                for option in ('fill', 'outline'):
                    try:
                        value = str(widget.itemcget(item, option)).lower()
                    except tk.TclError:
                        continue
                    if value in swap:
                        widget.itemconfigure(item, **{option: swap[value]})
        for child in widget.winfo_children():
            self._recolor(child, swap)

    def _topbar(self, w):
        P = self.px
        bar = tk.Frame(w, bg=RAIL, height=P(58))
        bar.pack(fill='x')
        bar.pack_propagate(False)
        self._rule(w)
        brand = tk.Frame(bar, bg=RAIL)
        brand.pack(side='left', padx=(P(20), 0))
        size = P(24)
        mark = tk.Canvas(brand, width=size, height=size, bg=RAIL, highlightthickness=0)
        # A four-pane window: fenestra.
        inset, mid, stroke = P(2), size / 2, max(2, P(2))
        mark.create_rectangle(inset, inset, size - inset, size - inset, outline=ACCENT, width=stroke)
        mark.create_line(mid, inset, mid, size - inset, fill=ACCENT, width=stroke)
        mark.create_line(inset, mid, size - inset, mid, fill=ACCENT, width=stroke)
        mark.create_rectangle(inset + P(3), inset + P(3), mid - P(3), mid - P(3), fill=ACCENT, outline='')
        mark.pack(side='left')
        self._label(brand, 'Fenestra', size=12, bold=True).pack(side='left', padx=(P(10), 0))
        self._label(brand, 'Z8 调试工作台', color=MUTED, size=10).pack(side='left', padx=(P(8), P(14)))
        self._label(brand, ' Z8 · C 3.11 ', color=MUTED, size=8, mono=True, bg=RAISED).pack(side='left')

        right = tk.Frame(bar, bg=RAIL)
        right.pack(side='right', padx=(0, P(18)))
        self.connect_button = self._button(right, '连接 Z8', self._connect, kind='primary')
        self.connect_button.pack(side='right')
        self.theme_button = self._button(right, self._theme_label(), self.toggle_theme,
                                         kind='ghost', small=True)
        self.theme_button.pack(side='right', padx=(P(8), 0))
        # A pill-shaped toggle; the native check box draws poorly on custom colours.
        self.auto_check = tk.Checkbutton(right, text='●  每 2 秒刷新', variable=self.auto,
                                         indicatoron=False, bg=RAIL, fg=FAINT, selectcolor=DONE_BG,
                                         activebackground=RAISED, activeforeground=TEXT,
                                         font=(FONT, 9), highlightthickness=0, bd=0, relief='flat',
                                         offrelief='flat', overrelief='flat', padx=P(10), pady=P(4),
                                         cursor='hand2')
        self.auto_check.pack(side='right', padx=P(10))
        self.auto.trace_add('write', lambda *args: self.auto_check.configure(
            fg=OK if self.auto.get() else FAINT))
        pill = tk.Frame(right, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        pill.pack(side='right')
        self.connection_badge = self._label(pill, '●  未连接', color=MUTED, size=9)
        self.connection_badge.pack(side='left', padx=(P(12), P(12)), pady=P(5))
        self.device_line = self._label(pill, '', color=MUTED, size=9, mono=True)

    def _statusbar(self, w):
        P = self.px
        bar = tk.Frame(w, bg=RAIL, height=P(30))
        bar.pack(side='bottom', fill='x')
        bar.pack_propagate(False)
        tk.Frame(w, bg=LINE, height=1).pack(side='bottom', fill='x')
        self.spinner = tk.Canvas(bar, width=P(14), height=P(14), bg=RAIL, highlightthickness=0)
        self.spinner.pack(side='left', padx=(P(16), P(8)))
        self.status_label = self._label(bar, textvariable=self.status, color=MUTED, size=9, anchor='w')
        self.status_label.pack(side='left', fill='x', expand=True)
        self._label(bar, '开发版 · 实机 USB 尚未验收', color=WARN, size=8).pack(side='right', padx=(P(14), P(16)))
        self._label(bar, 'FXP2 / USB PTP', color=FAINT, size=8, mono=True).pack(side='right')

    def _rail(self, body):
        P = self.px
        rail = tk.Frame(body, bg=RAIL, width=P(214))
        rail.pack(side='left', fill='y')
        rail.pack_propagate(False)
        self._caption(rail, '实时调试').pack(anchor='w', padx=P(24), pady=(P(22), P(8)))
        for page in PAGES[:3]:
            self._nav_item(rail, *page)
        self._caption(rail, '操作可见性').pack(anchor='w', padx=P(24), pady=(P(20), P(8)))
        self._nav_item(rail, *PAGES[3])
        foot = tk.Frame(rail, bg=RAIL)
        foot.pack(side='bottom', fill='x', padx=P(24), pady=P(20))
        for line in ('Ctrl+1–4   切换页面', 'Ctrl+Enter   执行命令', 'Ctrl+S   保存日志',
                     'Ctrl+L   清空当前视图', 'Ctrl+T   切换深浅色'):
            self._hint(foot, line).pack(anchor='w', pady=P(2))

    def _nav_item(self, parent, key, glyph, title, subtitle):
        P = self.px
        item = tk.Frame(parent, bg=RAIL, cursor='hand2')
        item.pack(fill='x', padx=P(10), pady=P(1))
        bar = tk.Frame(item, bg=RAIL, width=P(3))
        bar.pack(side='left', fill='y')
        icon = self._label(item, glyph, color=FAINT, size=10, bold=True, mono=True, width=3)
        icon.pack(side='left', padx=(P(8), P(6)), pady=P(9))
        texts = tk.Frame(item, bg=RAIL)
        texts.pack(side='left', fill='x', expand=True)
        name = self._label(texts, title, color=MUTED, size=10, anchor='w')
        name.pack(anchor='w')
        sub = self._label(texts, subtitle, color=FAINT, size=8, anchor='w')
        sub.pack(anchor='w')
        dot = self._label(item, '', color=OK, size=8)
        dot.pack(side='right', padx=P(12))
        parts = (item, bar, icon, texts, name, sub, dot)
        for part in parts:
            part.bind('<Button-1>', lambda event, k=key: self.show_page(k))
            part.bind('<Enter>', lambda event, k=key: self._nav_paint(k, True))
            part.bind('<Leave>', lambda event, k=key: self._nav_paint(k, False))
        self.nav_items[key] = dict(parts=parts, bar=bar, icon=icon, name=name)

    def _nav_paint(self, key, hover=False):
        item = self.nav_items[key]
        selected = key == self.current_page
        bg = SURFACE if selected else NAV_HOVER if hover else RAIL
        for part in item['parts']:
            part.configure(bg=bg)
        item['bar'].configure(bg=ACCENT if selected else bg)
        item['icon'].configure(fg=ACCENT if selected else FAINT)
        item['name'].configure(fg=TEXT if selected or hover else MUTED)

    def _inspector(self, body):
        P = self.px
        side = tk.Frame(body, bg=RAIL, width=P(300))
        side.pack(side='right', fill='y')
        side.pack_propagate(False)
        self._caption(side, '设备').pack(anchor='w', padx=P(22), pady=(P(22), P(8)))
        grid = tk.Frame(side, bg=RAIL)
        grid.pack(fill='x', padx=P(22))
        grid.columnconfigure(1, weight=1)
        self.card_values = {}
        for row, (key, label, value) in enumerate([
                ('model', '型号', '—'), ('firmware', '固件', '—'), ('active', 'Fenestra', '等待连接'),
                ('usb', 'USB Debug', '未连接'), ('agent', 'Linux 代理', '未启动'), ('seen', '最近确认', '—')]):
            self._label(grid, label, color=MUTED, size=9).grid(row=row, column=0, sticky='w', pady=P(4))
            value_label = self._label(grid, value, size=10, bold=key == 'model', mono=key == 'seen')
            value_label.grid(row=row, column=1, sticky='e', pady=P(4))
            self.card_values[key] = value_label
        self._hint(side, '电池、温度等遥测尚未接入。').pack(anchor='w', padx=P(22), pady=(P(6), 0))
        self._rule(side, padx=P(22), pady=P(18))

        self._caption(side, '连接前准备').pack(anchor='w', padx=P(22), pady=(0, P(8)))
        self.steps = []
        for number, text in enumerate(('相机内 Activate', 'USB Debug → On', '数据线连接电脑'), 1):
            row = tk.Frame(side, bg=RAIL)
            row.pack(fill='x', padx=P(22), pady=P(3))
            badge = self._label(row, str(number), color=MUTED, size=8, bold=True, bg=RAISED, width=2)
            badge.pack(side='left')
            self._label(row, text, color=MUTED, size=9).pack(side='left', padx=P(10))
            self.steps.append(badge)
        self._rule(side, padx=P(22), pady=P(18))

        head = tk.Frame(side, bg=RAIL)
        head.pack(fill='x', padx=P(22))
        self._caption(head, '活动').pack(side='left')
        self._button(head, '清空', self._clear_activity, kind='ghost', small=True).pack(side='right')
        self.activity = tk.Text(side, bg=RAIL, fg=MUTED, relief='flat', bd=0, highlightthickness=0,
                                font=(FONT, 9), wrap='word', state='disabled', cursor='arrow',
                                padx=P(22), pady=P(4), height=6, spacing1=P(3), takefocus=0)
        self.activity.pack(fill='both', expand=True, pady=(P(4), P(14)))
        for tag, color in (('ok', OK), ('error', ERROR), ('amber', WARN), ('info', INFO),
                           ('muted', FAINT), ('title', TEXT), ('detail', MUTED), ('time', FAINT)):
            self.activity.tag_configure(tag, foreground=color)
        self.activity.tag_configure('time', font=(self.mono, 8))
        self.activity.tag_configure('detail', lmargin1=P(16), lmargin2=P(16), spacing3=P(4))

    def _page_header(self, page, title, subtitle):
        P = self.px
        head = tk.Frame(page, bg=BG)
        head.pack(fill='x', pady=(0, P(16)))
        actions = tk.Frame(head, bg=BG)
        actions.pack(side='right', anchor='s')
        text = tk.Frame(head, bg=BG)
        text.pack(side='left', fill='x', expand=True)
        self._label(text, title, size=17, bold=True).pack(anchor='w')
        self._label(text, subtitle, color=MUTED, size=9, anchor='w', justify='left',
                    wraplength=P(600)).pack(anchor='w', pady=(P(4), 0))
        return actions

    def _composer(self, page, prompt, variable, history):
        P = self.px
        composer = tk.Frame(page, bg=INPUT, highlightthickness=1, highlightbackground=LINE)
        self._label(composer, prompt, color=ACCENT, bold=True, mono=True).pack(side='left', padx=(P(14), P(4)))
        entry = self._entry(composer, variable, bare=True)
        entry.pack(side='left', fill='x', expand=True, pady=P(6))
        entry.bind('<FocusIn>', lambda event: composer.configure(highlightbackground=ACCENT))
        entry.bind('<FocusOut>', lambda event: composer.configure(highlightbackground=LINE))
        self.history[history], self.history_at[history] = [], 0
        entry.bind('<Up>', lambda event: self._recall(variable, entry, history, -1))
        entry.bind('<Down>', lambda event: self._recall(variable, entry, history, 1))
        return composer, entry

    # ---- pages ------------------------------------------------------------

    def _terminal_page(self, page):
        P = self.px
        actions = self._page_header(page, 'RTOS 控制台',
            '在相机内的 Fenestra 调试任务中执行命令。输出范围是当前任务的 printf 内容，单任务缓冲区 8 KiB。')
        self._button(actions, '保存日志', self.save_log, kind='ghost').pack(side='right')
        self._button(actions, '清空视图', self.clear_view, kind='ghost').pack(side='right', padx=(0, P(4)))

        tools = tk.Frame(page, bg=BG)
        tools.pack(fill='x', pady=(0, P(10)))
        for text, action in (('刷新状态', 'refresh'), ('连接测试', 'echo'), ('查询任务', 'rtos_status')):
            self._button(tools, text, lambda a=action: self._submit(a), requirement='connected',
                         small=True).pack(side='left', padx=(0, P(6)))
        self._button(tools, '恢复结果', lambda: self._submit('recover'), requirement='recover',
                     small=True).pack(side='left')
        self._button(tools, '收尾卡上传', lambda: self._submit('card_resolve'), requirement='card_resolve',
                     small=True).pack(side='left', padx=(P(6), 0))
        metrics = tk.Frame(tools, bg=BG)
        metrics.pack(side='right')
        self.job_values = {}
        for key, label in (('job', '任务'), ('state', '状态'), ('produced', '输出'), ('dropped', '丢失')):
            self._hint(metrics, label).pack(side='left', padx=(P(16), P(6)))
            value = self._label(metrics, '—', color=MUTED, size=9, mono=True)
            value.pack(side='left')
            self.job_values[key] = value

        composer, self.command_entry = self._composer(page, 'rtos ›', self.command, 'rtos')
        self.command_entry.bind('<Return>', lambda event: self._execute())
        self.cancel_button = self._button(composer, '取消', self._cancel, small=True)
        self.cancel_button.pack(side='right', padx=(P(6), P(8)))
        self._button(composer, '执行', self._execute, kind='primary', requirement='command',
                     small=True).pack(side='right')
        self._hint(composer, 'ms', bg=INPUT).pack(side='right', padx=(P(4), P(12)))
        tk.Entry(composer, textvariable=self.timeout, width=6, bg=SURFACE, fg=TEXT,
                 insertbackground=ACCENT, relief='flat', bd=P(3), justify='right',
                 font=(self.mono, 9), highlightthickness=0).pack(side='right')
        self._hint(composer, '超时', bg=INPUT).pack(side='right', padx=(P(10), P(6)))

        footer = tk.Frame(page, bg=BG)
        footer.pack(side='bottom', fill='x', pady=(P(6), 0))
        self._hint(footer, 'Enter 执行 · ↑↓ 历史命令 · 超时 100–300000 ms').pack(side='right')
        self._hint(footer, '取消为协作式，不会强杀原生函数。').pack(side='left')
        composer.pack(side='bottom', fill='x', pady=(P(10), 0))
        frame, self.terminal = self._text(page)
        frame.pack(fill='both', expand=True)

    def _memory_page(self, page):
        P = self.px
        actions = self._page_header(page, '寄存器与内存',
            '读取运行中的系统寄存器与内存。读取分段进行，不保证是同一时刻的快照。')
        self._button(actions, '读取寄存器', lambda: self._submit('registers'),
                     requirement='connected').pack(side='right')

        card = tk.Frame(page, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill='x', pady=(0, P(14)))
        self.register_grid = tk.Frame(card, bg=SURFACE)
        self.register_grid.pack(fill='x', padx=P(16), pady=P(10))
        self._show_registers({name: None for name in REGISTERS})

        bar = tk.Frame(page, bg=BG)
        bar.pack(fill='x', pady=(0, P(10)))
        self._label(bar, '地址', color=MUTED, size=9).pack(side='left', padx=(0, P(8)))
        address = self._entry(bar, self.address, width=19)
        address.pack(side='left')
        address.bind('<Return>', lambda event: self._memory_read())
        self._label(bar, '长度', color=MUTED, size=9).pack(side='left', padx=(P(14), P(8)))
        self._entry(bar, self.count, width=6).pack(side='left')
        for preset in ('64', '256', '1008'):
            self._button(bar, preset, lambda v=preset: self.count.set(v), kind='ghost',
                         small=True).pack(side='left', padx=(P(2), 0))
        self._button(bar, '导出到文件…', self._memory_dump, requirement='connected').pack(side='right')
        self._button(bar, '›', lambda: self._memory_step(1), requirement='connected').pack(side='right', padx=(P(2), P(12)))
        self._button(bar, '读取', self._memory_read, kind='primary', requirement='connected').pack(side='right')
        self._button(bar, '‹', lambda: self._memory_step(-1), requirement='connected').pack(side='right', padx=(0, P(2)))

        self.memory_info = self._hint(page, '单次查看最多 1008 字节，导出最多 16 MiB；‹ › 按当前长度翻页。')
        self.memory_info.pack(side='bottom', anchor='w', pady=(P(6), 0))
        frame, self.memory_text = self._text(page, height=7)
        self.memory_text.configure(wrap='none')
        frame.pack(fill='both', expand=True)
        self._append(self.memory_text, '等待读取。地址支持 0x 前缀的十六进制。', 'muted')

    def _show_registers(self, values):
        P = self.px
        for child in self.register_grid.winfo_children():
            child.destroy()
        items = list(values.items())
        rows = (len(items) + 1) // 2
        for index, (name, value) in enumerate(items):
            row, column = index % rows, index // rows * 2
            self._label(self.register_grid, name, color=MUTED, size=9, mono=True).grid(
                row=row, column=column, sticky='w', padx=(0 if column == 0 else P(24), P(12)), pady=P(2))
            self._label(self.register_grid, value or '—', color=TEXT if value else FAINT, size=10,
                        mono=True).grid(row=row, column=column + 1, sticky='w', pady=P(2))
        self.register_grid.columnconfigure(1, weight=1)
        self.register_grid.columnconfigure(3, weight=1)

    def _linux_page(self, page):
        P = self.px
        self._page_header(page, 'Linux 代理',
            '上传并启动 RAM 代理后，命令在相机 Linux 中通过 /bin/sh -c 执行。机内启动链路尚待实机验证。')

        card = tk.Frame(page, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill='x', pady=(0, P(12)))
        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill='x', padx=P(16), pady=(P(14), P(8)))
        self._label(row, '代理程序', color=MUTED, size=9).pack(side='left', padx=(0, P(10)))
        self._entry(row, self.agent).pack(side='left', fill='x', expand=True, padx=(0, P(8)))
        self._button(row, '选择…', self._choose_agent, small=True).pack(side='left')
        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill='x', padx=P(16), pady=(0, P(14)))
        self._button(row, '启动代理', lambda: self._submit('linux_start', agent=self.agent.get()),
                     kind='primary', requirement='linux_start', small=True).pack(side='left')
        self._button(row, '读取桥状态', lambda: self._submit('linux_status'), requirement='connected',
                     small=True).pack(side='left', padx=P(6))
        self._button(row, '停止代理', lambda: self._submit('linux_stop'), requirement='linux_owned',
                     small=True).pack(side='left')
        self.linux_badge = self._label(row, textvariable=self.linux_summary, color=MUTED, size=9)
        self.linux_badge.pack(side='right')
        self.progress = ttk.Progressbar(row, style='Z.Horizontal.TProgressbar', length=P(140), maximum=100)
        self.progress.pack(side='right', padx=P(12))

        files = tk.Frame(page, bg=BG)
        files.pack(side='bottom', fill='x', pady=(P(8), 0))
        self._label(files, '机内路径', color=MUTED, size=9).pack(side='left', padx=(0, P(10)))
        self._entry(files, self.remote).pack(side='left', fill='x', expand=True, padx=(0, P(8)))
        self._button(files, '下载到新文件…', self._download, requirement='linux', small=True).pack(side='left')
        self._button(files, '上传新文件…', self._upload, requirement='linux', small=True).pack(side='left', padx=(P(6), 0))
        self._hint(page, '上传使用独占创建、fdatasync 和 SHA-256 回读校验；下载保存到新文件。传输上限 64 MiB。').pack(
            side='bottom', anchor='w', pady=(P(8), 0))

        composer, entry = self._composer(page, 'linux ›', self.linux_command, 'linux')
        entry.bind('<Return>', lambda event: self._linux_execute())
        self.linux_cancel_button = self._button(composer, '取消', self._cancel, small=True)
        self.linux_cancel_button.pack(side='right', padx=(P(6), P(8)))
        self._button(composer, '执行', self._linux_execute, kind='primary', requirement='linux',
                     small=True).pack(side='right')
        composer.pack(side='bottom', fill='x', pady=(P(10), 0))
        frame, self.linux_text = self._text(page, height=6)
        frame.pack(fill='both', expand=True)

    def show_page(self, key):
        for frame in self.pages.values():
            frame.pack_forget()
        self.pages[key].pack(fill='both', expand=True)
        self.current_page = key
        for name in self.nav_items:
            self._nav_paint(name)

    def _calls_page(self, page):
        self._label(page, '调用记录', size=18, bold=True).pack(anchor='w')
        self._label(page, '显示操作来源、参数、实时输出与执行结果。自动化调用和界面使用同一个相机连接。',
                    color=MUTED, size=9).pack(anchor='w', pady=(self.px(6), self.px(12)))
        self.bridge_info = self._label(page, '本地调用入口未开启', color=ACCENT, size=9)
        self.bridge_info.pack(anchor='w', pady=(0, self.px(12)))
        frame, self.calls_text = self._text(page)
        frame.pack(fill='both', expand=True)

    def _call_log(self, source, text, tag='muted'):
        self._log(source, text, tag, self.calls_text)

    def _bridge_tick(self):
        if not self.bridge or self.busy or self.closing:
            return
        try:
            row = self.bridge.take()
        except OSError as exc:
            self._local_error('调用日志写入失败，操作未执行：' + str(exc))
            return
        if row is None:
            return
        self.remote_request = row['id']
        self.show_page('calls')
        if row['action'] == 'workbench_status':
            self._call_log('自动化 #' + row['id'], 'workbench_status {}', 'accent')
            result = dict(connected=self.state['connected'], busy=self.busy,
                          state=self.state, camera_accessed=False)
            self._handle('result', {'action': 'workbench_status', 'value': result})
            self._handle('state', self.state)
            self._handle('idle', 'workbench_status')
        else:
            if not self._submit(row['action'], **row['arguments']):
                self._handle('error', dict(action=row['action'], error='工作台未能提交操作',
                                           kind='WorkbenchDispatchError', notes=[]))
                self._handle('state', self.state)
                self._handle('idle', row['action'])

    # ---- actions ----------------------------------------------------------

    def _connect(self):
        self._submit('disconnect' if self.state['connected'] else 'connect')

    def _submit(self, action, **arguments):
        if self.busy or self.closing:
            return False
        if action == 'connect':
            self.trouble = False
        source = '自动化 #' + self.remote_request if self.remote_request else '界面'
        detail = action + ' ' + json.dumps(arguments, ensure_ascii=False)
        if self.bridge:
            try:
                self.bridge.record('submit', source=source, request_id=self.remote_request,
                                   action=action, arguments=arguments)
            except OSError as exc:
                self._local_error('调用日志写入失败，操作未执行：' + str(exc))
                return False
        self._call_log(source, detail, 'accent')
        self.busy, self.active_action = True, action
        self.status.set(ACTION_LABELS[action] + '…')
        self.status_label.configure(fg=MUTED)
        self.worker.submit(action, **arguments)
        self._update_controls()
        return True

    def _execute(self):
        if not self._can('command') or self.busy:
            return
        try:
            timeout = int(self.timeout.get())
        except ValueError:
            self._local_error('超时请输入整数毫秒。')
            return
        line = self.command.get()
        if self._submit('execute', line=line, timeout_ms=timeout):
            self._remember('rtos', line)
            self._log('RTOS >', line, 'accent')

    def _linux_execute(self):
        line = self.linux_command.get()
        if self._submit('linux_execute', line=line):
            self._remember('linux', line)
            self._log('Linux >', line, 'accent', self.linux_text)

    def _remember(self, key, line):
        items = self.history[key]
        if line.strip() and (not items or items[-1] != line):
            items.append(line)
            del items[:-50]
        self.history_at[key] = len(items)

    def _recall(self, variable, entry, key, step):
        items = self.history[key]
        if items:
            self.history_at[key] = min(max(self.history_at[key] + step, 0), len(items))
            at = self.history_at[key]
            variable.set(items[at] if at < len(items) else '')
            entry.icursor('end')
        return 'break'

    def _cancel(self):
        if self.busy and self.active_action in CANCELLABLE:
            self.worker.cancel.set()
            self.status.set('已请求取消，等待目标返回…')

    def _memory_values(self):
        try:
            return int(self.address.get(), 0), int(self.count.get(), 0)
        except ValueError:
            self._local_error('地址和字节数请输入整数，十六进制需以 0x 开头。')
            return None

    def _memory_read(self):
        values = self._memory_values()
        if values:
            self._submit('memory_read', address=values[0], count=values[1])

    def _memory_step(self, direction):
        values = self._memory_values()
        if values:
            address = max(0, values[0] + direction * values[1])
            self.address.set(f'0x{address:x}')
            self._submit('memory_read', address=address, count=values[1])

    def _memory_dump(self):
        values = self._memory_values()
        if not values:
            return
        output = filedialog.asksaveasfilename(parent=self.window, title='导出内存到新文件',
            defaultextension='.bin', filetypes=[('二进制文件', '*.bin')], confirmoverwrite=False)
        if output:
            self._submit('memory_dump', address=values[0], count=values[1], output=output)

    def _show_memory(self, result):
        self._clear(self.memory_text)
        header = f'{"ADDRESS":<16}  ' + ' '.join(f'{i:02x}' for i in range(16)) + '  ASCII\n'
        self._append(self.memory_text, header, 'time')
        for line in result['display'].splitlines():
            self._append(self.memory_text, line[:16], 'muted')
            self._append(self.memory_text, line[16:65], 'output')
            self._append(self.memory_text, line[65:] + '\n', 'info')
        self.memory_text.see('1.0')
        self.memory_info.configure(text=f'{result["bytes"]} 字节 @ 0x{result["address"]:x} · '
                                        f'{datetime.now():%H:%M:%S} 读取 · 非原子快照')

    def _choose_agent(self):
        filename = filedialog.askopenfilename(parent=self.window, title='选择 AArch64 Linux 代理')
        if filename:
            self.agent.set(filename)

    def _download(self):
        output = filedialog.asksaveasfilename(parent=self.window, title='下载到新文件', confirmoverwrite=False)
        if output:
            self._submit('linux_download', remote=self.remote.get(), output=output)

    def _upload(self):
        source = filedialog.askopenfilename(parent=self.window, title='选择要上传的文件')
        if source:
            self._submit('linux_upload', source=source, remote=self.remote.get())

    # ---- state ------------------------------------------------------------

    def _can(self, requirement):
        connected = self.state['connected']
        card = self.state.get('card_upload') or {}
        if requirement == 'card_resolve':
            return connected and bool(card.get('blocking') or self.state.get('card_orphan'))
        if card.get('blocking') and requirement != 'recover':
            return False
        pending = self.state['pending'] or self.state['linux_pending']
        retained = self.state['rtos_job'] is not None or self.state['linux_job'] is not None
        if requirement == 'recover':
            return connected and (pending or retained)
        if requirement == 'command':
            return connected and not pending and not retained
        if requirement == 'linux_start':
            return connected and not pending and not retained and not self.state['linux_owned']
        if requirement == 'linux_owned':
            return connected and not pending and self.state['linux_owned']
        if requirement == 'linux':
            return connected and not pending and not retained and self.state['linux_ready']
        return connected and not pending

    def _uncertain(self):
        return self.state['connected'] and (self.trouble or self.state['pending'] or self.state['linux_pending'] or
            (self.state.get('card_upload') or {}).get('blocking', False))

    def _update_controls(self):
        for button, requirement in self.controls:
            self._enable(button, not self.busy and not self.closing and self._can(requirement))
        connected = self.state['connected']
        self.connect_button.kind = 'secondary' if connected else 'primary'
        self.connect_button.configure(text='断开连接' if connected else '连接 Z8')
        self._enable(self.connect_button, not (self.busy or self.closing))
        self._enable(self.cancel_button, self.busy and self.active_action in CANCELLABLE)
        self._enable(self.linux_cancel_button, self.busy and self.active_action == 'linux_execute')
        self.auto_check.configure(state='disabled' if self.closing else 'normal')

        report = self.state['report'] or {}
        values = self.card_values
        if connected:
            color = WARN if self._uncertain() else OK
            self.connection_badge.configure(text='●  连接待确认' if self._uncertain() else '●  已连接', fg=color)
            self.device_line.configure(text=report['model'] + ' · ' + report['firmware'])
            self.device_line.pack(side='left', padx=(0, self.px(12)))
            values['model'].configure(text=report['model'], fg=TEXT)
            values['firmware'].configure(text=report['firmware'], fg=TEXT)
            values['active'].configure(text='运行中' if report['fenestra_active'] else '未激活',
                                       fg=OK if report['fenestra_active'] else WARN)
            values['usb'].configure(text='待确认' if self._uncertain() else '已连通', fg=color)
        else:
            self.connection_badge.configure(text='●  未连接', fg=MUTED)
            self.device_line.pack_forget()
            for key in ('model', 'firmware'):
                values[key].configure(text='—', fg=FAINT)
            values['active'].configure(text='等待连接', fg=MUTED)
            values['usb'].configure(text='未连接', fg=MUTED)
        agent = (('运行中', OK) if self.state['linux_ready'] else
                 ('停止待确认', WARN) if self.state['linux_owned'] else ('未启动', MUTED))
        values['agent'].configure(text=agent[0], fg=agent[1])
        values['seen'].configure(text=self.last_seen if connected and self.last_seen else '—',
                                 fg=TEXT if connected else FAINT)
        self.linux_summary.set('代理已启动' if self.state['linux_ready'] else
                               '代理停止待确认' if self.state['linux_owned'] else '代理尚未启动')
        self.linux_badge.configure(fg=agent[1])
        for badge, number in zip(self.steps, '123'):
            badge.configure(text='✓' if connected else number, fg=OK if connected else MUTED,
                            bg=DONE_BG if connected else RAISED)
        retained = self.state['pending'] or self.state['rtos_job'] is not None
        self.nav_items['terminal']['parts'][-1].configure(text='●' if connected and retained else '', fg=WARN)
        self.nav_items['linux']['parts'][-1].configure(
            text='●' if self.state['linux_ready'] or self.state['linux_owned'] else '', fg=agent[1])

    def _show_job(self, result):
        label, role = JOB_STATES.get(result['state'], (result['state'], 'MUTED'))
        color = globals()[role]
        self.job_values['job'].configure(text=f'#{result["job"]}', fg=TEXT)
        self.job_values['state'].configure(text=label, fg=color)
        self.job_values['produced'].configure(text=f'{result["produced"]} B', fg=TEXT)
        self.job_values['dropped'].configure(text=str(result['dropped']),
                                             fg=OK if result['dropped'] == 0 else WARN)

    # ---- journal ----------------------------------------------------------

    def _log(self, source, text, tag='muted', widget=None):
        stamp = f'{datetime.now():%H:%M:%S}'
        target = widget or self.terminal
        if target.get('end-2c', 'end-1c') not in ('', '\n'):
            self._append(target, '\n')
        if self.journal and not self.journal[-1].endswith('\n'):
            self.journal.append('\n')
        self.journal.append(f'[{stamp}] [{source}] {text}\n')
        self._append(target, stamp + '  ', 'time')
        self._append(target, source + '  ', 'source')
        self._append(target, text + '\n', tag)

    def _activity(self, title, detail='', tone='ok'):
        pieces = [('●  ', tone), (title, 'title'), (f'   {datetime.now():%H:%M:%S}\n', 'time')]
        if detail:
            detail = detail.splitlines()[0]
            pieces.append(((detail[:117] + '…' if len(detail) > 118 else detail) + '\n', 'detail'))
        self.activity.configure(state='normal')
        for text, tag in reversed(pieces):
            self.activity.insert('1.0', text, tag)
        self.activity.delete('400.0', 'end')
        self.activity.configure(state='disabled')

    def _clear_activity(self):
        self._clear(self.activity)

    def _local_error(self, message):
        self.status.set(message)
        self.status_label.configure(fg=WARN)
        self._log('提示', message, 'amber')

    def _show_alert(self, text):
        self.alert_text.configure(text=text)
        if not self.alert_shown:
            self.alert.pack(fill='x', padx=self.px(28), pady=(self.px(16), 0), before=self.page_area)
            self.alert_shown = True

    def _hide_alert(self):
        if self.alert_shown:
            self.alert.pack_forget()
            self.alert_shown = False

    def _handle(self, kind, value):
        if self.bridge:
            try:
                self.bridge.observe(kind, value)
            except OSError as exc:
                self._local_error('审计日志写入失败：' + str(exc))
        if kind in ('result', 'error'):
            self._call_log('自动化结果' if self.remote_request else '界面结果',
                           json.dumps(value, ensure_ascii=False), 'error' if kind == 'error' else 'output')
        elif kind == 'output':
            self._call_log(value['source'], value['text'], 'output')
        if kind == 'state':
            self.state = value
            self._update_controls()
        elif kind == 'output':
            widget = self.linux_text if value['source'] == 'Linux' else self.terminal
            self.journal.append(f'[{value["source"]} / {"stderr" if value["stream"] else "stdout"}] ' + value['text'])
            self._append(widget, value['text'], 'error' if value['stream'] else 'output')
        elif kind == 'notice':
            self._log('调试', value, 'amber')
            self._activity('提示', value, 'info')
        elif kind == 'progress':
            self.status.set(f'正在上传 Linux 代理… {value}%')
            self.progress.configure(value=value)
        elif kind == 'error':
            self.trouble = self.state['connected']
            self.auto.set(False)
            first = value['error'].splitlines()[0] if value['error'] else value['kind']
            self.status.set(ACTION_LABELS[value['action']] + '失败 · ' + first)
            self.status_label.configure(fg=ERROR)
            self._show_alert(ACTION_LABELS[value['action']] + '失败：' + first)
            self._activity(ACTION_LABELS[value['action']] + '失败', first, 'error')
            self._log('错误', value['error'], 'error',
                      self.linux_text if value['action'].startswith('linux') else self.terminal)
            for note in value['notes']:
                self._log('提示', note, 'amber')
        elif kind == 'result':
            if value['action'] != 'workbench_status':
                self._result(value['action'], value['value'])
        elif kind == 'idle':
            self.busy, self.active_action = False, None
            self.remote_request = None
            if self.closing and value == 'disconnect':
                self.close_done = True
            self._update_controls()

    def _result(self, action, result):
        self.status.set(ACTION_LABELS[action] + '完成')
        self.status_label.configure(fg=MUTED)
        detail = ''
        if action in ('connect', 'refresh', 'echo'):
            self.trouble = False
            self.last_seen = f'{datetime.now():%H:%M:%S}'
            self._hide_alert()
        if action == 'disconnect':
            self.auto.set(False)
            self.last_seen = None
            self._hide_alert()
            self.progress.configure(value=0)
        if action in ('connect', 'refresh'):
            detail = result['model'] + ' · ' + result['firmware']
            if action == 'connect':
                self._log('USB', '身份核对通过 · ' + detail, 'ok')
                self._log('构建', result['build_identity'])
            elif self.auto.get():
                return  # periodic refreshes would flood the activity list
        elif action == 'registers':
            self._show_registers(result)
        elif action == 'memory_read':
            self._show_memory(result)
            detail = f'{result["bytes"]} 字节 @ 0x{result["address"]:x}'
            self._log('内存', '读取 ' + detail)
        else:
            widget = self.linux_text if action.startswith('linux') else self.terminal
            self._log(ACTION_LABELS[action], json.dumps(result, ensure_ascii=False), 'muted', widget)
            if action in ('execute', 'native_call', 'recover', 'rtos_status') and 'produced' in result:
                self._show_job(result)
                detail = f'任务 #{result["job"]} · {JOB_STATES.get(result["state"], (result["state"],))[0]} · {result["produced"]} B'
            if action == 'linux_start':
                self.progress.configure(value=100)
        self._activity(ACTION_LABELS[action], detail, 'ok')

    # ---- loops ------------------------------------------------------------

    def _drain(self):
        if self.destroyed:
            return
        self._bridge_tick()
        for _ in range(200):
            try:
                kind, value = self.worker.events.get_nowait()
            except queue.Empty:
                break
            self._handle(kind, value)
        if self.closing and self.close_done:
            self.worker.stop()
            self._finish_close()
            return
        self.poll_id = self.window.after(40, self._drain)

    def _auto_refresh(self):
        if self.destroyed:
            return
        if self.auto.get() and not self.busy and self._can('command') and not self.closing:
            self._submit('refresh')
        self.auto_id = self.window.after(2000, self._auto_refresh)

    def _spin(self):
        if self.destroyed:
            return
        canvas, P = self.spinner, self.px
        size, margin = P(14), P(2)
        if self.busy:
            self.spin_angle = (self.spin_angle - 24) % 360
            self.spin_key = None
            canvas.delete('all')
            canvas.create_oval(margin, margin, size - margin, size - margin, outline=LINE, width=P(2))
            canvas.create_arc(margin, margin, size - margin, size - margin, start=self.spin_angle,
                              extent=110, style='arc', outline=ACCENT, width=P(2))
        else:
            color = (WARN if self._uncertain() else OK) if self.state['connected'] else FAINT
            if color != self.spin_key:
                self.spin_key = color
                canvas.delete('all')
                radius, middle = P(4), size / 2
                canvas.create_oval(middle - radius, middle - radius, middle + radius, middle + radius,
                                   fill=color, outline='')
        self.spin_id = self.window.after(70, self._spin)

    def clear_view(self):
        widget = {'linux': self.linux_text, 'memory': self.memory_text,
                  'calls': self.calls_text}.get(self.current_page, self.terminal)
        self._clear(widget)

    def export_log(self, destination):
        header = ('Fenestra Z8 调试工作台 · 会话日志\n'
                  f'导出时间：{datetime.now().isoformat(timespec="seconds")}\n'
                  '来源：本窗口实时操作。\n\n')
        Path(destination).write_text(header + ''.join(self.journal), encoding='utf-8')

    def save_log(self):
        destination = filedialog.asksaveasfilename(parent=self.window, title='保存会话日志',
            defaultextension='.txt', initialfile=f'z8-debug-{datetime.now():%Y%m%d-%H%M%S}.txt',
            filetypes=[('UTF-8 日志', '*.txt')])
        if destination:
            try:
                self.export_log(destination)
                self.status.set('日志已保存：' + destination)
                self._activity('日志已保存', destination, 'ok')
            except OSError as exc:
                self._local_error(str(exc))

    def close(self):
        if self.closing:
            return
        if self.busy:
            # Let the owning worker finish; never close COM from the UI thread.
            self.worker.cancel.set()
            self.status.set('正在等待当前操作结束并关闭连接…')
            self.closing = True
            self.worker.tasks.put(('disconnect', {}))
            self._update_controls()
            return
        self.closing = True
        self.busy, self.active_action = True, 'disconnect'
        self.worker.submit('disconnect')
        self._update_controls()

    def _finish_close(self):
        self.destroyed = True
        if self.bridge:
            self.bridge.close()
        for pending in (self.auto_id, self.spin_id):
            if pending is not None:
                self.window.after_cancel(pending)
        self.window.destroy()


def _dpi_aware():
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def system_theme():
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return 'light' if winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] else 'dark'
        except OSError:
            pass
    return 'dark'


def saved_theme():
    """The last theme picked in the window, else the Windows app theme."""
    try:
        theme = json.loads(SETTINGS.read_text(encoding='utf-8')).get('theme')
    except (OSError, ValueError, AttributeError):
        theme = None
    return theme if theme in PALETTES else system_theme()


def save_theme(theme):
    # A preference only: failing to store it never interrupts the session.
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps({'theme': theme}), encoding='utf-8')
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page', choices=[key for key, *_ in PAGES], default='terminal')
    parser.add_argument('--theme', choices=tuple(PALETTES), help='默认沿用上次选择或 Windows 应用主题')
    parser.add_argument('--no-automation', action='store_true', help='关闭本地工作台调用入口')
    args = parser.parse_args(argv)
    _dpi_aware()
    window = tk.Tk()
    app = DebugApp(window, theme=args.theme or saved_theme(), automation=not args.no_automation)
    app.show_page(args.page)
    window.mainloop()


if __name__ == '__main__':
    main()
