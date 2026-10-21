"""DAQmx同期パルス計測（PULSE）の画面と通信処理。"""
import re

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox, QFormLayout,
    QPushButton, QComboBox, QLabel, QDoubleSpinBox,
)


def _spin(minimum, maximum, step, decimals=4):
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    return spin


def _plot(parent):
    # 呼び出し側が先にレイアウトを用意していれば、それを再利用する。
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
    figure = Figure()
    canvas = FigureCanvasQTAgg(figure)
    axis = figure.add_subplot(111)
    axis.grid(True)
    toolbar = NavigationToolbar2QT(canvas, parent)
    layout.addWidget(canvas, 1)
    layout.addWidget(toolbar)
    return figure, axis, canvas, toolbar


class PulseStimulusPanel(QWidget):
    """単一パルスの同期計測設定と刺激・取得波形を表示する。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(split, 1)

        upper = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(upper)
        settings = QGroupBox('Pulse Stimulus')
        settings_layout = QVBoxLayout(settings)
        form = QFormLayout()
        self.output = QComboBox()
        form.addRow('出力チャンネル', self.output)
        settings_layout.addLayout(form)

        timing = QHBoxLayout()
        self.dt = _spin(.001, 10, .001, 3)
        self.dt.setValue(.01)
        timing.addWidget(QLabel('dT (ms)'))
        timing.addWidget(self.dt)
        self.duration = _spin(.01, 100_000, .1, 2)
        self.duration.setValue(100)
        timing.addWidget(QLabel('T (ms)'))
        timing.addWidget(self.duration)
        settings_layout.addLayout(timing)

        pulse_row = QHBoxLayout()
        pulse = QGroupBox('Pulse 1')
        pulse_form = QFormLayout(pulse)
        self.pulse_delay = _spin(0, 1_000_000, .1, 3)
        self.pulse_delay.setValue(20)
        self.pulse_width = _spin(.001, 1_000_000, .1, 3)
        self.pulse_width.setValue(4)
        self.pulse_amplitude = _spin(-10_000, 10_000, 1, 3)
        self.pulse_amplitude.setValue(290)
        pulse_form.addRow('Delay (ms)', self.pulse_delay)
        pulse_form.addRow('Width (ms)', self.pulse_width)
        pulse_form.addRow('Amplitude (mV)', self.pulse_amplitude)
        pulse_row.addWidget(pulse, 1)
        # 従来の左右配置を保つため表示するが、現行コマンドでは2発目を送信しない。
        pulse2 = QGroupBox('Pulse 2')
        pulse2_form = QFormLayout(pulse2)
        pulse2_form.addRow(QLabel('現在のPULSEコマンドでは未対応'))
        pulse2.setEnabled(False)
        pulse_row.addWidget(pulse2, 1)
        settings_layout.addLayout(pulse_row)

        self.warning = QLabel('配線前に出力を確認し、接続先の許容電圧に合わせて振幅を下げてください。')
        self.warning.setWordWrap(True)
        settings_layout.addWidget(self.warning)
        self.fire_button = QPushButton('PULSE計測開始')
        settings_layout.addWidget(self.fire_button)
        self.status = QLabel('DAQmx機器へ接続してください')
        self.status.setWordWrap(True)
        settings_layout.addWidget(self.status)
        settings_layout.addStretch(1)
        upper.addWidget(settings)

        preview = QWidget()
        QVBoxLayout(preview)
        self.stim_fig, self.stim_ax, self.stim_canvas, self.stim_toolbar = _plot(preview)
        self.stim_canvas.setMinimumHeight(160)
        upper.addWidget(preview)
        upper.setSizes([440, 650])

        results = QWidget()
        result_layout = QVBoxLayout(results)
        self.fig, self.ax, self.canvas, self.plot_toolbar = _plot(results)
        self.ax.set_box_aspect(1)
        self.ax.set_xlabel('Time (s)')
        self.ax.set_ylabel('Voltage (V)')
        self.save_button = QPushButton('取得データをCSV保存…')
        self.save_image_button = QPushButton('プロット画像保存…')
        self.save_button.setEnabled(False)
        self.save_image_button.setEnabled(False)
        save_row = QHBoxLayout()
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.save_image_button)
        result_layout.addLayout(save_row)
        split.addWidget(results)
        split.setSizes([440, 400])

        self.dt.valueChanged.connect(self.update_preview)
        self.duration.valueChanged.connect(self.update_preview)
        self.pulse_delay.valueChanged.connect(self.update_preview)
        self.pulse_width.valueChanged.connect(self.update_preview)
        self.pulse_amplitude.valueChanged.connect(self.update_preview)
        self.update_preview()

    def set_outputs(self, devices):
        """DAQMXINFOのAOタスクから選択肢を作る。item dataは(機器, AO番号)。"""
        selected = self.output.currentData()
        self.output.clear()
        for device in devices:
            if device.static or not device.analog or device.taskType not in ('AO', 'DAO'):
                continue
            for channel in range(device.N_data):
                label = f'{device.name}/ao{channel} (AO task {device.taskNo})'
                self.output.addItem(label, (device, channel))
        match = self.output.findData(selected) if selected is not None else -1
        if match < 0:
            match = next(
                (i for i in range(self.output.count()) if self.output.itemData(i)[1] == 52),
                0 if self.output.count() else -1,
            )
        if match >= 0:
            self.output.setCurrentIndex(match)

    def update_preview(self, *_args):
        """設定値から、出力予定の単一パルスを上段に描く。"""
        duration = self.duration.value()
        delay = self.pulse_delay.value()
        width = self.pulse_width.value()
        amplitude = self.pulse_amplitude.value()
        end = min(delay + width, duration)
        times = [0, delay, delay, end, end, duration]
        values = [0, 0, amplitude, amplitude, 0, 0]
        self.stim_ax.clear()
        self.stim_ax.grid(True)
        self.stim_ax.plot(times, values, drawstyle='steps-post')
        self.stim_ax.set_xlim(0, duration)
        self.stim_ax.set_xlabel('Time (ms)')
        self.stim_ax.set_ylabel('Amplitude (mV)')
        self.stim_canvas.draw_idle()

    def show_results(self, samples):
        """時刻、AO電圧、AI電圧を同じ軸に重ねて表示する。"""
        self.ax.clear()
        self.ax.grid(True)
        if samples:
            times = [row[0] for row in samples]
            ao_values = [row[1] for row in samples]
            ai_values = [row[2] for row in samples]
            self.ax.plot(times, ao_values, label='AO voltage')
            self.ax.plot(times, ai_values, label='AI voltage')
            self.ax.legend(loc='best')
        self.ax.set_xlabel('Time (s)')
        self.ax.set_ylabel('Voltage (V)')
        self.canvas.draw_idle()


class PulseMeasurementJob(QObject):
    """PULSEコマンドを順に送信し、有限計測結果を取得するワーカー。"""
    done = pyqtSignal(object, object)

    def __init__(self, tcp, device, channel, frequency, duration, delay, width, amplitude):
        super().__init__()
        self.tcp = tcp
        self.device = device
        self.channel = channel
        self.frequency = frequency
        self.duration = duration
        self.delay = delay
        self.width = width
        self.amplitude = amplitude

    def run(self):
        try:
            self.done.emit(self.measure(), None)
        except Exception as error:
            self.done.emit(None, error)

    def _send_and_wait_ok(self, command):
        self.tcp.send(command)
        response = self.tcp.recv()
        if response is None:
            raise RuntimeError(f'サーバーから応答がありません: {command}')
        # recv()は#OK終端まで読み切る。情報行付きACKも許容する。
        if any(line.lstrip().startswith('!') for line in response):
            raise RuntimeError(f'{command}が拒否されました: {response}')

    def measure(self):
        if self.delay + self.width > self.duration * 1000:
            raise ValueError('遅延とパルス幅の合計が計測時間を超えています')
        # PULSEはAO task番号と物理AO番号を受け取る。
        self._send_and_wait_ok(f'SET PULSE {self.device.taskNo} {self.channel}')
        self._send_and_wait_ok(f'{self.frequency} {self.duration:.12g}')
        self._send_and_wait_ok(
            f'{self.delay:.12g} {self.width:.12g} {self.amplitude:.12g}'
        )

        self.tcp.send('GET PULSE')
        response = self.tcp.recv()
        if not response:
            raise RuntimeError('GET PULSEの応答がありません')
        header_index = next(
            (i for i, line in enumerate(response) if line.strip().startswith('#GETPULSE')),
            None,
        )
        if header_index is None:
            raise RuntimeError(f'GET PULSE応答に#GETPULSEヘッダーがありません: {response[:3]}')
        header = response[header_index].split()
        if len(header) < 2:
            raise RuntimeError(f'GET PULSEのサンプル数が読めません: {response[header_index]}')
        expected_count = int(header[1])

        # 設定値の行は読み飛ばし、数値3列のサンプル行を抽出する。
        samples = []
        for line in response[header_index + 1:]:
            fields = re.split(r'[\s,]+', line.strip())
            if len(fields) != 3:
                continue
            try:
                samples.append(tuple(float(field) for field in fields))
            except ValueError:
                continue
        if len(samples) < expected_count:
            raise RuntimeError(
                f'GET PULSEは{expected_count}サンプルの予定ですが、{len(samples)}行しか読めませんでした'
            )
        if expected_count == 0:
            samples = []
        elif len(samples) > expected_count:
            samples = samples[-expected_count:]
        return {'samples': samples, 'configuration': response[header_index + 1] if len(response) > header_index + 1 else ''}
