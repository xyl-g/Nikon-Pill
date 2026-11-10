"""Main window for Nikon Pill — Nikon Z8 C 3.11 firmware patch builder.

Frozen builds bundle the NFP toolkit (no picker); source runs pick a toolkit
directory from the header instead.
"""
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QTextEdit, QVBoxLayout, QWidget,
)

import theme
from backend import (FENESTRA_INPUT_SHA, FENESTRA_PATCH_SHA, FENESTRA_RESULT_SHA,
                     FROZEN, TAG_RE, VERSION_RE, detect_toolkit, output_name)
from worker import TaskWorker

APP_NAME = 'Nikon Pill'

TOOLKITS = [
    ('nfp', 'NikonFirmwarePatch', '四功能组任意组合 · 自定版本号 · 机内菜单原生生效'),
    ('fenestra', 'Fenestra Z8 (cold-retry3)', '官方 r10 基线一键应用 · 机内 Z8Mouldes 模块菜单 · 需配套 .wlm 插件'),
]

FEATURES = [
    ('framing', '画面构图', '开放门 3:2 · N-RAW 四档画质 · 反挤压 1.25–2.00x'),
    ('video-quality', '视频品质', 'ProRes 四档 + HQ 星标 · H.265 五档码率'),
    ('log-colour', 'Log 色彩', 'N-Log · S-Log3 · Log3G10 与取景辅助'),
    ('photo-tools', '拍照工具', 'RAW 预拍 C15/C30 · 像素偏移合成'),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} — Nikon Z8 固件补丁构建器')
        self.resize(1000, 900)
        self.setMinimumSize(780, 560)
        self.info = None
        self.worker = None
        self._check_worker = None
        self._tk_dir = ''
        self._fw_path = None
        self._build_dir = None
        self._build_bin = None
        self._sha = ''
        self._identity = ''
        self._user_cancelled = False
        self._check_ok = False
        self._verify_ctx = None
        self._spin = 0
        self._spinner = QTimer(self)
        self._spinner.timeout.connect(self._tick_spinner)
        self.setAcceptDrops(True)
        self._tk_kind = 'nfp'          # default toolkit; user may switch
        self._fenestra_patch = None
        self._fenestra_expected = None
        self._init_ui()
        self.setStyleSheet(theme.APP_QSS)
        # Default toolkit card selected state (after QSS so polish applies).
        self.tk_cards['nfp'].setProperty('selected', True)
        self.tk_cards['nfp'].style().unpolish(self.tk_cards['nfp'])
        self.tk_cards['nfp'].style().polish(self.tk_cards['nfp'])
        self.fx_widget.setVisible(False)
        if FROZEN:
            self._refresh_toolkit()
        self._update_state()

    # ---------- construction ----------

    def _init_ui(self):
        # Scrollable central area: the window can shrink without squeezing
        # cards, and tall content scrolls instead of bouncing layouts.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        central = QWidget()
        scroll.setWidget(central)
        scroll.setMinimumWidth(760)
        self.setCentralWidget(scroll)
        root = QVBoxLayout(central)
        root.setContentsMargins(30, 22, 30, 22)
        root.setSpacing(14)

        root.addLayout(self._build_header())
        root.addWidget(self._build_toolkit_card())
        root.addWidget(self._build_firmware_card())
        self._features_card = self._build_features_card()
        root.addWidget(self._features_card)
        root.addWidget(self._build_output_card())
        root.addLayout(self._build_action_row())
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)
        root.addWidget(self._build_log_card())
        root.addWidget(self._build_result_card())
        root.addStretch(1)

    def _build_toolkit_card(self):
        card = self._card('构建方案', '两套工具包二选一 · 同一份原厂固件')
        grid = QGridLayout()
        grid.setSpacing(10)
        self.tk_cards = {}
        for i, (key, name, desc) in enumerate(TOOLKITS):
            cardf = QFrame()
            cardf.setProperty('featCard', True)
            cardf.setCursor(Qt.PointingHandCursor)
            v = QVBoxLayout(cardf)
            v.setContentsMargins(14, 11, 14, 11)
            v.setSpacing(3)
            title = QLabel(name)
            title.setProperty('featTitle', True)
            v.addWidget(title)
            sub = QLabel(desc)
            sub.setProperty('featDesc', True)
            sub.setWordWrap(True)
            v.addWidget(sub)
            cardf.mousePressEvent = (lambda ev, k=key, c=cardf: self._pick_toolkit_kind(k, c))
            grid.addWidget(cardf, 0, i)
            self.tk_cards[key] = cardf
        card.layout().addLayout(grid)
        self.tk_status = QLabel('')
        self.tk_status.setProperty('faint', True)
        self.tk_status.setWordWrap(True)
        card.layout().addWidget(self.tk_status)

        # Fenestra-only extras: manual .fxpatch picker (source runs)
        self.fx_row = QHBoxLayout()
        self.fx_edit = QLineEdit()
        self.fx_edit.setPlaceholderText('cold-retry3.fxpatch 路径（内置工具包已自带，无需选择）')
        self.fx_btn = QPushButton('选择…')
        self.fx_row.addWidget(self.fx_edit, 1)
        self.fx_row.addWidget(self.fx_btn)
        self.fx_widget = QWidget()
        self.fx_widget.setLayout(self.fx_row)
        card.layout().addWidget(self.fx_widget)
        self.fx_widget.hide()
        return card

    def _pick_toolkit_kind(self, key, card):
        if self.worker is not None:
            return
        for k, c in self.tk_cards.items():
            c.setProperty('selected', k == key)
            c.style().unpolish(c)
            c.style().polish(c)
        self._tk_kind = key
        self._tk_dir = ''          # header-picked dirs are kind-specific
        self.info = None
        self._check_ok = False
        self._fenestra_patch = None
        self.fx_widget.setVisible(key == 'fenestra' and not FROZEN)
        self._refresh_toolkit()

    def _build_header(self):
        row = QHBoxLayout()
        # Hand-drawn pill (same artwork as the app icon) instead of the
        # emoji glyph — Segoe UI Emoji on Windows renders a pink pill.
        # Rendered at native 2x for crispness, no downscale blur.
        logo = QLabel()
        logo.setObjectName('logo')
        logo.setFixedSize(44, 44)
        from make_icon import render as render_icon
        pix = QPixmap.fromImage(render_icon(88))   # 2x for Retina
        pix.setDevicePixelRatio(2.0)
        logo.setPixmap(pix)
        row.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel(APP_NAME)
        title.setProperty('h1', True)
        col.addWidget(title)
        self.subtitle = QLabel('Nikon Z8 · C 3.11 固件补丁构建器')
        self.subtitle.setProperty('sub', True)
        col.addWidget(self.subtitle)
        row.addLayout(col)
        row.addStretch(1)
        if not FROZEN:
            b = QPushButton('工具包…')
            b.clicked.connect(self._pick_toolkit)
            row.addWidget(b, 0, Qt.AlignTop)
        return row

    def _build_firmware_card(self):
        card = self._card('固件文件', '原厂 Z_8_0311.bin · 选择后自动校验')

        self.fw_row = QFrame()
        self.fw_row.setProperty('fileRow', True)
        row = QHBoxLayout(self.fw_row)
        row.setContentsMargins(14, 10, 12, 10)
        icon = QLabel('📄')
        icon.setStyleSheet('font-size: 20px;')
        row.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.fw_name = QLabel('未选择固件')
        self.fw_name.setProperty('featTitle', True)
        self.fw_meta = QLabel('点击右侧「选择…」或直接把文件拖入窗口')
        self.fw_meta.setProperty('faint', True)
        col.addWidget(self.fw_name)
        col.addWidget(self.fw_meta)
        row.addLayout(col, 1)
        self.fw_btn = QPushButton('选择…')
        row.addWidget(self.fw_btn)
        self.recheck_btn = QPushButton('↻  重新校验')
        self.recheck_btn.setEnabled(False)
        row.addWidget(self.recheck_btn)
        card.layout().addWidget(self.fw_row)

        self.err_panel = QFrame()
        self.err_panel.setProperty('errorPanel', True)
        ep = QVBoxLayout(self.err_panel)
        ep.setContentsMargins(14, 12, 14, 12)
        ep.setSpacing(6)
        self.err_title = QLabel('')
        self.err_title.setProperty('errTitle', True)
        ep.addWidget(self.err_title)
        self.err_expect = QLabel('')
        self.err_expect.setProperty('errLine', True)
        self.err_expect.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ep.addWidget(self.err_expect)
        self.err_actual = QLabel('')
        self.err_actual.setProperty('errLine', True)
        self.err_actual.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ep.addWidget(self.err_actual)
        self.err_note = QLabel('')
        self.err_note.setProperty('errNote', True)
        self.err_note.setWordWrap(True)
        ep.addWidget(self.err_note)
        self.err_panel.hide()
        card.layout().addWidget(self.err_panel)
        return card

    def _build_features_card(self):
        card = self._card('补丁功能组', '至少选 1 组 · 共 15 种组合')
        self.ft_count = QLabel('已选 0 / 4 组')
        self.ft_count.setProperty('hint', True)
        card.layout().itemAt(0).layout().addWidget(self.ft_count)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.feature_boxes = {}
        for i, (key, name, desc) in enumerate(FEATURES):
            cardf = QFrame()
            cardf.setProperty('featCard', True)
            cardf.setCursor(Qt.PointingHandCursor)
            v = QVBoxLayout(cardf)
            v.setContentsMargins(14, 11, 14, 11)
            v.setSpacing(3)
            title = QLabel(name)
            title.setProperty('featTitle', True)
            v.addWidget(title)
            sub = QLabel(desc)
            sub.setProperty('featDesc', True)
            sub.setWordWrap(True)
            v.addWidget(sub)
            cardf.mousePressEvent = (lambda ev, k=key, c=cardf: self._toggle_feature(k, c))
            grid.addWidget(cardf, i // 2, i % 2)
            self.feature_boxes[key] = cardf
        card.layout().addLayout(grid)

        actions = QHBoxLayout()
        self.ft_all_btn = QPushButton('全选')
        self.ft_none_btn = QPushButton('清空')
        for b in (self.ft_all_btn, self.ft_none_btn):
            b.setFixedWidth(70)
        actions.addWidget(self.ft_all_btn)
        actions.addWidget(self.ft_none_btn)
        actions.addStretch(1)
        card.layout().addLayout(actions)
        return card

    def _build_output_card(self):
        card = self._card('版本与输出', '版本号即相机菜单显示的固件版本')
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(4)

        def field_label(text):
            l = QLabel(text)
            l.setProperty('hint', True)
            return l

        # Version/tag rows live in their own collapsible block: the Fenestra
        # scheme is content-pinned, so these NFP-only fields hide entirely.
        self._version_fields = QWidget()
        vg = QGridLayout(self._version_fields)
        vg.setContentsMargins(0, 0, 0, 0)
        vg.setHorizontalSpacing(16)
        vg.setVerticalSpacing(4)
        vg.addWidget(field_label('公开版本'), 0, 0)
        vg.addWidget(field_label('内部 tag'), 0, 1)
        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText('NN.NN，例如 04.06')
        vg.addWidget(self.version_edit, 1, 0)
        self.tag_edit = QLineEdit('p0')
        vg.addWidget(self.tag_edit, 1, 1)
        self.version_note = QLabel('格式 NN.NN')
        self.version_note.setProperty('faint', True)
        vg.addWidget(self.version_note, 2, 0)
        self.tag_note = QLabel('仅记录用，不显示在相机菜单')
        self.tag_note.setProperty('faint', True)
        vg.addWidget(self.tag_note, 2, 1)
        g.addWidget(self._version_fields, 0, 0, 3, 2)

        g.addWidget(field_label('输出位置'), 3, 0, 1, 2)
        out_row = QHBoxLayout()
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText('选择保存位置（可已存在；构建时自动创建时间戳子目录）')
        self.out_btn = QPushButton('更改…')
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(self.out_btn)
        g.addLayout(out_row, 4, 0, 1, 2)
        self.out_preview = QLabel('输出：—')
        self.out_preview.setProperty('faint', True)
        self.out_preview.setWordWrap(True)
        g.addWidget(self.out_preview, 5, 0, 1, 2)
        card.layout().addLayout(g)
        return card

    def _build_action_row(self):
        row = QHBoxLayout()
        self.ready_icon = QLabel('')
        self.ready_icon.setStyleSheet(f'color: {theme.WARN}; font-size: 14px;')
        self.ready_hint = QLabel('')
        self.ready_hint.setProperty('hint', True)
        row.addWidget(self.ready_icon)
        row.addWidget(self.ready_hint, 1)
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.setEnabled(False)
        row.addWidget(self.cancel_btn)
        self.build_btn = QPushButton('构建固件')
        self.build_btn.setObjectName('primary')
        row.addWidget(self.build_btn)
        return row

    def _build_log_card(self):
        card = QFrame()
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(8)
        title = QLabel('构建日志')
        title.setProperty('cardTitle', True)
        v.addWidget(title)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont('Menlo', 12))
        self.log.setFixedHeight(150)
        v.addWidget(self.log)
        return card

    def _build_result_card(self):
        card = QFrame()
        card.setProperty('card', True)
        card.hide()
        self.result_card = card
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        self.result_title = QLabel('')
        self.result_title.setProperty('cardTitle', True)
        v.addWidget(self.result_title)
        self.result_label = QLabel('')
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label.setProperty('hint', True)
        v.addWidget(self.result_label)
        row = QHBoxLayout()
        self.open_btn = QPushButton('打开输出目录')
        self.open_btn.clicked.connect(self._open_output)
        self.copy_btn = QPushButton('复制 SHA-256')
        self.copy_btn.clicked.connect(self._copy_sha)
        self.verify_btn = QPushButton('重新校验')
        self.verify_btn.clicked.connect(self._start_verify)
        self.verify_btn.hide()
        row.addWidget(self.open_btn)
        row.addWidget(self.copy_btn)
        row.addWidget(self.verify_btn)
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _card(self, title, subtitle):
        card = QFrame()
        card.setProperty('card', True)
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        row = QHBoxLayout()
        t = QLabel(title)
        t.setProperty('cardTitle', True)
        s = QLabel(subtitle)
        s.setProperty('faint', True)
        row.addWidget(t)
        row.addWidget(s, 0, Qt.AlignBottom)
        row.addStretch(1)
        v.addLayout(row)
        return card

    # ---------- wiring ----------

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_wired', False):
            self._wired = True
            self.fw_btn.clicked.connect(self._pick_firmware)
            self.recheck_btn.clicked.connect(self._run_check)
            self.out_btn.clicked.connect(self._pick_output)
            self.fx_btn.clicked.connect(self._pick_fxpatch)
            self.ft_all_btn.clicked.connect(lambda: self._set_all(True))
            self.ft_none_btn.clicked.connect(lambda: self._set_all(False))
            for w in (self.version_edit, self.tag_edit, self.out_edit):
                w.textEdited.connect(self._update_state)
            self.build_btn.clicked.connect(self._start_build)
            self.cancel_btn.clicked.connect(self._cancel)

    def _tick_spinner(self):
        # Rotation-ish feedback on the re-check button while verifying.
        self._spin = (self._spin + 1) % 8
        self.recheck_btn.setText(('↻', '↺')[self._spin % 2] + '  校验中' + '·' * (self._spin // 2))

    # ---------- actions ----------

    def _pick_toolkit(self):
        title = ('选择 Fenestra 工具包目录' if self._tk_kind == 'fenestra'
                 else '选择 NikonFirmwarePatch 目录')
        path = QFileDialog.getExistingDirectory(self, title, str(Path.home() / 'git'))
        if path:
            self._tk_dir = path
            self._refresh_toolkit()

    def _pick_fxpatch(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Fenestra 补丁文件',
                                              str(Path.home() / 'Downloads'),
                                              'Fenestra 补丁 (*.fxpatch);;所有文件 (*)')
        if path:
            self.fx_edit.setText(path)
            self._fenestra_patch = Path(path)
            if self.info is not None:
                self.info.patch_file = self._fenestra_patch
                for note in list(self.info.notes):
                    pass
                self._update_state()

    def _pick_firmware(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择原厂固件',
                                              str(Path.home() / 'Downloads'),
                                              '固件 (*.bin);;所有文件 (*)')
        if path:
            self._set_firmware(Path(path))

    def _set_firmware(self, path: Path):
        self._fw_path = path
        size = path.stat().st_size if path.is_file() else 0
        self.fw_name.setText(path.name)
        self.fw_meta.setText(f'{path} · {size / 1048576:.1f} MB' if size else str(path))
        self._check_ok = False
        self.err_panel.hide()
        self.recheck_btn.setEnabled(True)
        self._run_check()

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, '选择保存位置',
                                                self.out_edit.text() or str(Path.home() / 'Downloads'))
        if path:
            self.out_edit.setText(path)
            self._update_state()

    def _toggle_feature(self, key, card):
        if self.worker is not None:
            return
        card.setProperty('selected', not card.property('selected'))
        card.style().unpolish(card)
        card.style().polish(card)
        self._update_state()

    def _set_all(self, on):
        for card in self.feature_boxes.values():
            card.setProperty('selected', on)
            card.style().unpolish(card)
            card.style().polish(card)
        self._update_state()

    def _selected(self):
        return [k for k, c in self.feature_boxes.items() if c.property('selected')]

    def _refresh_toolkit(self):
        # Per-kind toolkit directory: a header-picked dir only matches its own
        # kind; otherwise fall back to the conventional ~/git location.
        default_dir = {'fenestra': Path.home() / 'git' / 'Fenestra-Z8-C311-source',
                       'nfp': Path.home() / 'git' / 'NikonFirmwarePatch-toolkit-v0.4.0-rc.2'}
        picked = Path(self._tk_dir) if self._tk_dir else None
        if picked is not None and picked.is_dir() and self._kind_matches_dir(picked):
            root = picked
        else:
            root = None if FROZEN else default_dir.get(self._tk_kind)
        try:
            self.info = detect_toolkit(None if FROZEN else root,
                                       kind=self._tk_kind)
        except (FileNotFoundError, ValueError, OSError) as exc:
            self.info = None
            self.subtitle.setText(f'工具包无效 · {exc}')
            self.subtitle.setStyleSheet(f'color: {theme.BAD};')
            self.tk_status.setText('')
            self._update_state()
            return
        # Toolkit status line + branch-specific UI adjustments.
        notes = '；'.join(self.info.notes) or '就绪'
        self.subtitle.setStyleSheet('')
        self.subtitle.setText('Nikon Z8 · C 3.11 固件补丁构建器')
        self.tk_status.setText(('✓ ' if self.info.ready else '⚠ ') + notes)
        self.tk_status.setStyleSheet('' if self.info.ready else f'color: {theme.WARN};')
        is_fenestra = self.info.kind == 'fenestra'
        # Fenestra is content-pinned: collapse the NFP-only blocks entirely
        # (feature groups, version/tag fields, fxpatch picker) instead of
        # showing disabled widgets.
        self._features_card.setVisible(not is_fenestra)
        self._version_fields.setVisible(not is_fenestra)
        self.fx_widget.setVisible(is_fenestra and not FROZEN and not self.info.ready)
        self._update_state()

    def _kind_matches_dir(self, path: Path) -> bool:
        """Does a manually-picked directory belong to the active toolkit kind?"""
        marker = 'patcher.py' if self._tk_kind == 'fenestra' else 'patch.py'
        return (path / marker).is_file()

    def _run_check(self):
        if self.info is None:
            self._refresh_toolkit()
            if self.info is None:
                return
        path = self._fw_path
        if path is None or not path.is_file():
            return
        self.err_panel.hide()
        self._check_ok = False
        self.recheck_btn.setEnabled(False)
        self._log('info', f'读取 {path.name}（{path.stat().st_size/1048576:.1f} MB），开始解密 + SHA-256')
        self._spinner.start(280)
        self._check_worker = TaskWorker('check', self.info, path)
        self._check_worker.line.connect(self._log_line)
        self._check_worker.done.connect(self._check_done)
        self._check_worker.start()
        self._update_state()

    def _check_done(self, result):
        self._spinner.stop()
        self.recheck_btn.setText('↻  重新校验')
        self.recheck_btn.setEnabled(True)
        report = result.report or {}
        if result.ok and report.get('status') == 'SKIP':
            # Fenestra has no read-only engine check; verifying the content
            # hash directly is exactly what its patcher would do anyway.
            digest = self._sha256(self._fw_path) if self._fw_path else ''
            if digest == FENESTRA_INPUT_SHA:
                self._check_ok = True
                self.err_panel.hide()
                self._log('info', f'校验通过 · 官方 C 3.11 输入（{digest[:16]}…）')
            else:
                self._check_ok = False
                self._show_check_error({}, '')
                self._log('warn', 'SHA-256 与官方记录不一致，已阻止构建')
        elif result.ok and report.get('status') == 'COMPATIBLE':
            self._check_ok = True
            self.err_panel.hide()
            self._log('info', f'校验通过 · 原厂身份 {report.get("identity", "?")}')
        else:
            self._check_ok = False
            self._show_check_error(report, result.raw)
            self._log('warn', '解密完成，SHA-256 与官方记录不一致，已阻止构建')
        self._update_state()

    def _show_check_error(self, report, raw):
        expect = ''
        if self.info.kind == 'fenestra':
            # Fenestra has no input.json; its target pins the same official bin.
            from backend import FENESTRA_INPUT_SHA
            expect = FENESTRA_INPUT_SHA
        else:
            try:
                import json
                spec = json.loads((self.info.root / 'resources' / 'input.json').read_text())
                expect = spec.get('sha256', '')
            except (OSError, ValueError):
                pass
        actual = self._sha256(self._fw_path) if self._fw_path else ''
        self.err_title.setText('⚠  校验失败 · SHA-256 与官方记录不一致')
        self.err_expect.setText(f'期望  {_abbrev(expect) or "—"}（Nikon 官方 Z8 C 3.11）')
        self.err_actual.setText(f'实际  {_abbrev(actual) or "无法读取文件哈希"}')
        self.err_note.setText('文件可能下载不完整或被修改，请重新下载官方 C 3.11 更新包后点「重新校验」；'
                              '若确认文件无误仍失败，请携带上面两个哈希反馈。')
        self.err_note.setMinimumHeight(46)
        self.err_panel.show()

    @staticmethod
    def _sha256(path):
        import hashlib
        try:
            h = hashlib.sha256()
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return ''

    # ---------- state ----------

    def _update_state(self):
        features = self._selected()
        version = self.version_edit.text().strip()
        tag = self.tag_edit.text().strip()
        out_text = self.out_edit.text().strip()
        version_ok = bool(VERSION_RE.fullmatch(version))
        tag_ok = bool(TAG_RE.fullmatch(tag))
        base = Path(out_text) if out_text else None
        out_ok = bool(out_text) and not (base.exists() and not base.is_dir())
        fw_file = self._fw_path is not None and self._fw_path.is_file()
        fenestra = self._tk_kind == 'fenestra'

        self.version_edit.setProperty('invalid', bool(version) and not version_ok)
        self.tag_edit.setProperty('invalid', bool(tag) and not tag_ok)
        for w in (self.version_edit, self.tag_edit):
            w.style().unpolish(w)
            w.style().polish(w)

        if fenestra:
            self.version_note.setText('Fenestra 方案版本固定（cold-retry3 基线）')
            self.version_note.setStyleSheet('')
        elif version_ok:
            self.version_note.setText(f'相机将显示 C {version}')
            self.version_note.setStyleSheet(f'color: {theme.OK};')
        else:
            self.version_note.setText('格式 NN.NN')
            self.version_note.setStyleSheet('')

        self.ft_count.setText('由 .fxpatch 决定' if fenestra else f'已选 {len(features)} / 4 组')
        if fenestra:
            self.out_preview.setText(
                f'输出：{base / "Z_8_0311-cold-retry3.bin"}（生成后拷到存储卡根目录并改名 Z_8_0311.bin）'
                if out_text else '输出：—')
        elif version_ok and out_text:
            self.out_preview.setText(
                f'输出：{base}/v{version}-{tag or "p0"}_<时间戳>/'
                f'{output_name(version)} + manifest.json + SHA256SUMS.txt')
        else:
            self.out_preview.setText('输出：—')

        reasons = []
        if self.info is None or not self.info.ready:
            reasons.append('工具包未就绪')
        if not fw_file:
            reasons.append('未选择固件')
        elif not self._check_ok:
            reasons.append('固件校验未通过，修复后才能构建')
        if not fenestra:
            if not features:
                reasons.append('至少选择 1 个功能组')
            if not version_ok:
                reasons.append('版本号需为 NN.NN')
            if not tag_ok:
                reasons.append('tag 需为字母+数字')
        if not out_text:
            reasons.append('请选择保存位置')
        elif not out_ok:
            reasons.append('保存位置不是文件夹')
        self.ready_icon.setText('⚠' if reasons else '')
        self.ready_hint.setText('；'.join(reasons))
        self.build_btn.setEnabled(not reasons)

    # ---------- logging ----------

    def _log(self, level, text):
        stamp = time.strftime('%H:%M:%S')
        color = {'warn': theme.WARN, 'error': theme.BAD, 'info': theme.MUTED, 'ok': theme.OK}[level]
        prefix = {'warn': 'warn', 'error': ' err', 'info': 'info', 'ok': '  ok'}[level]
        self.log.append(
            f'<span style="color:{color}; font-weight:600;">{prefix}</span>'
            f'<span style="color:{theme.FAINT};"> {stamp} </span>'
            f'<span style="color:{theme.BODY};">{_esc(text)}</span>')

    def _log_line(self, line):
        s = line.strip()
        if not s:
            return
        if s.startswith('ERROR:'):
            self._log('warn', s[6:].strip())
            return
        if s[0] in '{}[]' or s[0] == '"' or s == ',' or s.endswith(','):
            return
        if '": ' in s or s.startswith(('name', 'start', 'bytes', 'sha256', 'status',
                                       'output', 'features', 'profiles', 'modules',
                                       'identity', 'old_sha256', 'new_sha256')):
            return
        self._log('info', s)

    # ---------- build ----------

    def _new_output_dir(self, base: Path, version: str, tag: str) -> Path:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        path = base / f'v{version}-{tag}_{stamp}'
        n = 2
        while path.exists():
            path = base / f'v{version}-{tag}_{stamp}-{n}'
            n += 1
        return path

    def _start_build(self):
        if self.worker is not None:
            return
        fenestra = self._tk_kind == 'fenestra' and (self.info and self.info.kind == 'fenestra')
        base = Path(self.out_edit.text().strip())
        if fenestra:
            # Single pinned output file; patcher.py refuses to overwrite
            # and does not create parent directories itself.
            base.mkdir(parents=True, exist_ok=True)
            out_bin = base / 'Z_8_0311-cold-retry3.bin'
            if out_bin.exists():
                stamp = time.strftime('%Y%m%d-%H%M%S')
                out_bin = base / f'Z_8_0311-cold-retry3_{stamp}.bin'
            self._build_dir = base
            self._build_bin = out_bin
            self._fenestra_expected = FENESTRA_RESULT_SHA
            self._log('info', '开始应用 Fenestra cold-retry3 补丁（.fxpatch）')
            self._log('info', f'输出文件 {out_bin}')
        else:
            order = [k for k, _, _ in FEATURES]
            features = sorted(self._selected(), key=order.index)
            version = self.version_edit.text().strip()
            tag = self.tag_edit.text().strip()
            out_dir = self._new_output_dir(base, version, tag)
            self._build_dir = out_dir
            self._build_bin = out_dir / output_name(version)
            self._fenestra_expected = None
            self._log('info', f'开始构建 {output_name(version)} · {" + ".join(features)}')
            self._log('info', f'输出目录 {out_dir}')
        self._identity = ''
        self._sha = ''
        self.result_card.hide()
        self._set_busy(True)
        self.worker = TaskWorker('build', self.info, self._fw_path,
                                 features=sorted(self._selected(),
                                                 key=[k for k, _, _ in FEATURES].index),
                                 version=self.version_edit.text().strip(),
                                 tag=self.tag_edit.text().strip(),
                                 output_dir=self._build_dir, output_bin=self._build_bin)
        self.worker.line.connect(self._log_line)
        self.worker.done.connect(self._build_done)
        self.worker.start()
        self._update_state()

    def _set_busy(self, busy):
        for w in (self.fw_btn, self.out_btn, self.version_edit, self.tag_edit, self.out_edit):
            w.setEnabled(not busy)
        self.recheck_btn.setEnabled(not busy and self._fw_path is not None)
        fenestra = self._tk_kind == 'fenestra'
        for card in self.feature_boxes.values():
            card.setEnabled(not busy and not fenestra)
        self.ft_all_btn.setEnabled(not busy and not fenestra)
        self.ft_none_btn.setEnabled(not busy and not fenestra)
        self.fx_btn.setEnabled(not busy)
        self.fx_edit.setEnabled(not busy)
        self.build_btn.setVisible(not busy)
        self.cancel_btn.setEnabled(busy)
        self.progress.setVisible(busy)

    def _build_done(self, result):
        self.worker = None
        if self._user_cancelled:
            self._user_cancelled = False
            self._set_busy(False)
            self._log('warn', '构建已取消')
            self._update_state()
            return
        if not result.ok:
            self._set_busy(False)
            self._log('error', '构建失败')
            self._update_state()
            QMessageBox.warning(self, '构建失败',
                                'patch.py 返回失败，详情见构建日志。\n\n'
                                '常见原因：固件非官方原包；磁盘空间不足；输出位置无写入权限。')
            return

        r = result.report or {}
        self._sha = r.get('result_sha256') or r.get('sha256') or r.get('output_sha256', '')
        self._identity = r.get('output_identity', '') or ('cold-retry3' if r.get('result_sha256') else '')
        if getattr(self, '_fenestra_expected', None):
            # Fenestra: content-pinned verification — compare the engine's
            # reported result hash against the official r10 baseline.
            self._verify_ctx = dict(features=('cold-retry3 base',), version='fixed', tag='')
            if self._sha == FENESTRA_RESULT_SHA:
                self._log('ok', f'构建完成 · 结果哈希与官方基线一致 · {self._sha[:16]}…')
                self.result_card.show()
                self.result_title.setText('✓  应用补丁完成 · 结果与官方基线一致')
                self.result_label.setText(self._result_text('PASS（内容哈希比对一致）'))
                self._log('ok', '独立校验 PASS')
            else:
                self._log('warn', f'结果哈希非官方基线：{self._sha[:16]}…')
                self.result_card.show()
                self.result_title.setText('应用补丁完成 · 结果哈希非官方基线')
                self.result_label.setText(self._result_text('非基线（fxpatch 可能被修改过）'))
            self.verify_btn.show()
            self._set_busy(False)
            self._update_state()
            return
        self._verify_ctx = dict(features=tuple(self._selected()), version=self.version_edit.text().strip(),
                                tag=self.tag_edit.text().strip())
        self._log('ok', f'构建完成 · {r.get("output_identity", "—")} · {self._sha[:16]}…')
        self.result_card.show()
        self.result_title.setText('构建完成 · 正在独立校验…')
        self.result_label.setText(self._result_text('校验中'))
        self._start_verify()

    def _result_text(self, verify_state):
        r = self._verify_ctx or {}
        return (f'{self._build_bin}\n'
                f'SHA-256  {self._sha}\n'
                f'身份  {self._identity or "—"} · 功能  {", ".join(r.get("features", ()))}\n'
                f'独立校验  {verify_state}')

    def _start_verify(self):
        if self.worker is not None or self._verify_ctx is None or self._build_bin is None:
            return
        if not self._build_bin.is_file():
            return
        self._set_busy(True)
        self.verify_btn.hide()
        self.result_title.setText('正在独立校验输出固件…')
        self.result_label.setText(self._result_text('校验中…'))
        ctx = self._verify_ctx
        self.worker = TaskWorker('verify', self.info, self._fw_path,
                                 features=ctx['features'], version=ctx['version'],
                                 tag=ctx['tag'], output_bin=self._build_bin)
        self.worker.line.connect(self._log_line)
        self.worker.done.connect(self._verify_done)
        self.worker.start()

    def _verify_done(self, result):
        self.worker = None
        self._set_busy(False)
        r = result.report or {}
        self._identity = r.get('output_identity', '')
        if result.ok and r.get('status') == 'PASS':
            self.result_title.setText('✓  构建完成 · 独立校验 PASS')
            self.result_label.setText(self._result_text('PASS（字节级比对通过）'))
            self._log('ok', '独立校验 PASS')
        else:
            self.result_title.setText('构建完成 · 独立校验未通过')
            self.result_label.setText(self._result_text('未通过，详见日志'))
            self._log('error', '独立校验未通过')
        self.verify_btn.show()
        self.result_card.show()
        self._update_state()

    def _open_output(self):
        if self._build_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._build_dir)))

    def _copy_sha(self):
        QGuiApplication.clipboard().setText(self._sha)

    def _cancel(self):
        self._user_cancelled = True
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)

    # ---------- drag & drop ----------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() == '.bin':
                self._set_firmware(path)
                break

    def closeEvent(self, event: QCloseEvent):
        if self.worker is not None and self.worker.isRunning():
            ret = QMessageBox.question(self, '退出', '任务正在进行，确定退出？',
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        event.accept()


def _abbrev(value):
    """8…5 hex abbreviation for hashes; returns '' for empty input."""
    if not value:
        return ''
    return value if len(value) <= 16 else f'{value[:8]}…{value[-5:]}'


def _esc(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
