"""DAQmxを使ったPRC計測画面とサーバー通信。"""
import re

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox, QFormLayout,
    QPushButton, QComboBox, QLabel, QDoubleSpinBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QFileDialog,
)


def _spin(minimum, maximum, step, decimals=3):
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    return spin


class PRCPanel(QWidget):
    """PRC計測条件、位相に対するPRCプロット、数値結果を表示する。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        settings = QGroupBox('PRC計測設定')
        form = QFormLayout(settings)
        self.output = QComboBox()
        form.addRow('AO出力チャンネル', self.output)
        self.max_phase = _spin(.001, 1_000_000, .1)
        self.max_phase.setValue(25)
        form.addRow('最大位相時間 (ms)', self.max_phase)
        self.phase_step = _spin(.001, 1_000_000, .1)
        self.phase_step.setValue(1)
        form.addRow('位相刻み (ms)', self.phase_step)
        self.repeats = QSpinBox()
        self.repeats.setRange(1, 100_000)
        self.repeats.setValue(8)
        form.addRow('繰り返し回数', self.repeats)
        self.threshold = _spin(-10_000, 10_000, 1)
        self.threshold.setValue(70)
        form.addRow('AIしきい値 (mV)', self.threshold)
        self.pulse_width = _spin(.001, 1_000_000, .1)
        self.pulse_width.setValue(4)
        form.addRow('パルス幅 (ms)', self.pulse_width)
        self.pulse_amplitude = _spin(-10_000, 10_000, 1)
        self.pulse_amplitude.setValue(100)
        form.addRow('基準AOからの振幅 (mV)', self.pulse_amplitude)
        self.baseline = QLabel('AO基準値: --')
        form.addRow(self.baseline)
        self.note = QLabel('AI task 0の先頭チャンネルを使用します。基準AO値は出力更新済みの値を使用します。')
        self.note.setWordWrap(True)
        form.addRow(self.note)
        self.run_button = QPushButton('PRC計測開始')
        form.addRow(self.run_button)
        self.status = QLabel('DAQmx機器へ接続してください')
        self.status.setWordWrap(True)
        form.addRow(self.status)
        split.addWidget(settings)

        results = QWidget()
        results_layout = QVBoxLayout(results)
        self.fig = Figure()
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.grid(True)
        self.ax.set_xlabel('Phase (ms)')
        self.ax.set_ylabel('PRC value')
        self.toolbar = NavigationToolbar2QT(self.canvas, results)
        results_layout.addWidget(self.canvas, 3)
        results_layout.addWidget(self.toolbar)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            '位相 (ms)', '初回周期変化 平均 (s)', '初回周期変化 分散 (s²)',
            '平均周期 (s)', '周期 分散 (s²)', 'PRC値',
        ])
        self.table.setMinimumHeight(150)
        results_layout.addWidget(self.table, 2)
        save_row = QHBoxLayout()
        self.save_button = QPushButton('結果をCSV保存…')
        self.save_button.setEnabled(False)
        save_row.addWidget(self.save_button)
        save_row.addStretch(1)
        results_layout.addLayout(save_row)
        split.addWidget(results)
        split.setSizes([360, 850])
        self.result_rows = []
        self.output.currentIndexChanged.connect(self.update_baseline)

    def set_outputs(self, devices):
        """DAQMXINFOの動的AOタスクからチャンネルを作る。"""
        selected = self.output.currentData()
        self.output.clear()
        for device in devices:
            if device.static or not device.analog or device.taskType != 'AO':
                continue
            for channel in range(device.N_data):
                self.output.addItem(
                    f'{device.name}/ao{channel} (AO task {device.taskNo})',
                    (device, channel),
                )
        match = self.output.findData(selected) if selected is not None else -1
        if match < 0:
            match = next(
                (i for i in range(self.output.count()) if self.output.itemData(i)[1] == 52),
                0 if self.output.count() else -1,
            )
        if match >= 0:
            self.output.setCurrentIndex(match)
        self.update_baseline()

    def update_baseline(self, *_args):
        """選択中AOチャンネルのクライアント保持値を基準値として表示する。"""
        selected = self.output.currentData()
        if not selected:
            self.baseline.setText('AO基準値: --')
            return
        device, channel = selected
        values = device.get_ch(channel)
        baseline = values[0] if values else 0
        self.baseline.setText(f'AO基準値: {baseline:g} V（出力更新後の値）')

    def show_results(self, rows):
        """6列のPRC結果を表とPRC曲線に反映する。"""
        self.result_rows = rows
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.table.setItem(row_index, column, QTableWidgetItem(f'{value:.9g}'))
        self.ax.clear()
        self.ax.grid(True)
        if rows:
            self.ax.plot([row[0] for row in rows], [row[5] for row in rows], marker='o')
        self.ax.set_xlabel('Phase (ms)')
        self.ax.set_ylabel('PRC value')
        self.fig.tight_layout()
        self.canvas.draw_idle()
        self.save_button.setEnabled(bool(rows))
        self.status.setText(f'完了: {len(rows)}位相点')


class PRCMeasurementJob(QObject):
    """SET/RUN/GET PRCを順に送り、結果6列を取り出す。"""
    done = pyqtSignal(object, object)
    progress = pyqtSignal(str)

    def __init__(self, tcp, device, channel, max_phase, phase_step, repeats,
                 threshold, pulse_width, pulse_amplitude):
        super().__init__()
        self.tcp = tcp
        self.device = device
        self.channel = channel
        self.max_phase = max_phase
        self.phase_step = phase_step
        self.repeats = repeats
        self.threshold = threshold
        self.pulse_width = pulse_width
        self.pulse_amplitude = pulse_amplitude

    def run(self):
        try:
            self.done.emit(self.measure(), None)
        except Exception as error:
            self.done.emit(None, error)

    def _send_and_wait_ok(self, command, timeout=30):
        self.tcp.send(command.rstrip('\r\n') + '\n')
        self.progress.emit(f'サーバー応答待ち: {command}')
        response = self.tcp.recv(timeout=timeout)
        if response is None:
            raise RuntimeError(f'{command}に対する応答がありません')
        if any(line.lstrip().startswith('!') for line in response):
            raise RuntimeError(f'{command}が拒否されました: {response}')

    def measure(self):
        self._send_and_wait_ok(f'SET PRC {self.device.taskNo} {self.channel}')
        self._send_and_wait_ok(
            f'{self.max_phase:.12g} {self.phase_step:.12g} {self.repeats}'
        )
        self._send_and_wait_ok(
            f'{self.threshold:.12g} {self.pulse_width:.12g} {self.pulse_amplitude:.12g}'
        )
        self._send_and_wait_ok('RUN PRC')
        self.progress.emit('GET PRC: 計測完了と結果を待っています…')
        self.tcp.send('GET PRC\n')
        response = self.tcp.recv(timeout=300)
        if not response:
            raise RuntimeError('GET PRCの応答がありません')
        rows = []
        expected_count = None
        for line in response:
            header = re.match(r'^#GETPRC\s+(\d+)', line.strip(), re.IGNORECASE)
            if header:
                expected_count = int(header.group(1))
                continue
            fields = re.split(r'[\s,]+', line.strip())
            if len(fields) != 6:
                continue
            try:
                rows.append(tuple(float(field) for field in fields))
            except ValueError:
                continue
        if expected_count is not None and len(rows) != expected_count:
            raise RuntimeError(
                f'GET PRCは{expected_count}点の予定ですが、{len(rows)}行を受信しました'
            )
        if not rows:
            raise RuntimeError(f'GET PRC応答に6列の結果行がありません: {response[:3]}')
        return rows

