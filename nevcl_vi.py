"""電圧–電流測定の画面部品とバックグラウンド測定処理。"""
import asyncio

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox, QFormLayout,
    QPushButton, QLabel, QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox,
)


def _spin(minimum, maximum, step, decimals=4):
    """指定した刻み幅と表示桁数の数値入力欄を作る。"""
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    return spin


class VoltageCurrentPanel(QWidget):
    """電圧掃引、Keithley電流測定、V-I結果表示を行うパネル。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        settings = QGroupBox('測定設定')
        form = QFormLayout(settings)
        self.visa_address = QLineEdit('TCPIP::157.82.169.219::INSTR')
        form.addRow('Keithley VISA', self.visa_address)
        self.host = QLineEdit('localhost')
        form.addRow('NEVサーバー', self.host)

        self.sweep_channel = QComboBox()
        form.addRow('掃引出力', self.sweep_channel)
        self.segments = []
        segment_group = QGroupBox('掃引区間')
        segment_layout = QVBoxLayout(segment_group)
        defaults = ((.15, .25, 11), (.25, .35, 21), (.35, .45, 11))
        for index, (start, stop, count) in enumerate(defaults, 1):
            start_spin = _spin(-10, 10, .001)
            start_spin.setValue(start)
            stop_spin = _spin(-10, 10, .001)
            stop_spin.setValue(stop)
            points = QSpinBox()
            points.setRange(2, 10000)
            points.setValue(count)
            row = QHBoxLayout()
            row.addWidget(QLabel(str(index)))
            row.addWidget(start_spin)
            row.addWidget(QLabel('→'))
            row.addWidget(stop_spin)
            row.addWidget(QLabel('点'))
            row.addWidget(points)
            segment_layout.addLayout(row)
            self.segments.append((start_spin, stop_spin, points))
        form.addRow(segment_group)

        self.repeats = QSpinBox()
        self.repeats.setRange(1, 1000)
        self.repeats.setValue(2)
        form.addRow('反復回数', self.repeats)
        controls_layout.addWidget(settings)
        buttons = QHBoxLayout()
        self.run_button = QPushButton('測定開始')
        self.stop_button = QPushButton('停止')
        self.stop_button.setEnabled(False)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        controls_layout.addLayout(buttons)
        save_buttons = QHBoxLayout()
        self.save_csv_button = QPushButton('CSV保存…')
        self.save_image_button = QPushButton('画像保存…')
        self.save_csv_button.setEnabled(False)
        self.save_image_button.setEnabled(False)
        save_buttons.addWidget(self.save_csv_button)
        save_buttons.addWidget(self.save_image_button)
        controls_layout.addLayout(save_buttons)
        self.status = QLabel('待機中')
        self.status.setWordWrap(True)
        controls_layout.addWidget(self.status)
        controls_layout.addStretch(1)
        split.addWidget(controls)

        plot_widget = QWidget()
        plot_layout = QVBoxLayout(plot_widget)
        self.fig = Figure()
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.grid(True)
        self.toolbar = NavigationToolbar2QT(self.canvas, plot_widget)
        plot_layout.addWidget(self.canvas, 1)
        plot_layout.addWidget(self.toolbar)
        self.ax.set_box_aspect(1)
        self.ax.set_xlabel('Vdiff [V]')
        self.ax.set_ylabel('Istim [A]')
        split.addWidget(plot_widget)
        split.setSizes([490, 700])

    def set_outputs(self, devices):
        """DAQMXINFOから取得した動的AOチャンネルを各出力選択欄に並べる。"""
        combo = self.sweep_channel
        selected = combo.currentData()
        combo.clear()
        for device in devices:
            if device.static or not device.analog or device.taskType not in ('AO', 'DAO'):
                continue
            for channel in range(device.N_data):
                data = (device.taskType, device.taskNo, channel)
                label = f'{device.name}/{device.taskType}-{channel}'
                combo.addItem(label, data)
        match = combo.findData(selected) if selected is not None else -1
        if match < 0:
            match = next(
                (i for i in range(combo.count()) if combo.itemData(i)[2] == 52),
                0 if combo.count() else -1,
            )
        if match >= 0:
            combo.setCurrentIndex(match)

    def show_results(self, voltage, mean, std):
        """V-I測定結果を誤差棒付きで描画する。"""
        self.ax.clear()
        self.ax.grid(True)
        valid = [i for i, value in enumerate(mean) if value is not None]
        if valid:
            x = [voltage[i] for i in valid]
            y = [mean[i] for i in valid]
            yerr = [std[i] or 0 for i in valid]
            self.ax.errorbar(x, y, yerr=yerr, capsize=3)
        self.ax.set_xlabel('Vdiff [V]')
        self.ax.set_ylabel('Istim [A]')
        self.ax.set_box_aspect(1)
        self.canvas.draw_idle()


class VoltageCurrentJob(QObject):
    """DAQ出力とKeithley測定をGUIスレッド外で実行する。"""
    progress = pyqtSignal(str)
    point_updated = pyqtSignal(object)
    done = pyqtSignal(object, object)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.stop_requested = False

    def request_stop(self):
        self.stop_requested = True

    def run(self):
        try:
            self.done.emit(asyncio.run(self._measure()), None)
        except Exception as error:
            self.done.emit(None, error)

    async def _measure(self):
        import telnetlib3
        import pyvisa

        settings = self.settings
        reader = writer = instrument = manager = None
        try:
            reader, writer = await telnetlib3.open_connection(settings['host'], 9000)
            # viplot.pyと同じく既定のVISA実装を使う（NI-VISA等とPyVISA-Pyの両方に対応）。
            try:
                manager = pyvisa.ResourceManager()
            except Exception as error:
                raise RuntimeError(
                    'PyVISAの通信バックエンドを読み込めません。'
                    'NI-VISAなどのIVI実装、またはPyVISA-Pyが実行環境にあるか確認してください。'
                ) from error
            instrument = manager.open_resource(settings['visa_address'])
            instrument.write('smua.source.output = smua.OUTPUT_ON')

            async def send(output, value):
                task_type, task_no, channel = output
                dynamic_type = 'D' + task_type.removeprefix('D')
                commands = (
                    f'SET {dynamic_type} {task_no} {channel}',
                    f'{value:.12g} #OK',
                    f'RUN {dynamic_type} *',
                )
                for command in commands:
                    writer.write(command + '\n')
                    await writer.drain()
                    try:
                        await asyncio.wait_for(reader.read(1024), timeout=5)
                    except asyncio.TimeoutError as error:
                        raise TimeoutError(f'NEVサーバーの応答がありません: {command}') from error
                    await asyncio.sleep(.5)
                    if self.stop_requested:
                        raise InterruptedError('測定を停止しました')

            segments = settings['segments']
            await send(settings['sweep_channel'], segments[0][0])

            sweep_voltages = []
            for segment_start, segment_stop, segment_points in segments:
                values = [
                    segment_start + (segment_stop - segment_start) * i / (segment_points - 1)
                    for i in range(segment_points)
                ]
                for value in values:
                    if not sweep_voltages or value != sweep_voltages[-1]:
                        sweep_voltages.append(value)

            measurements = [[] for _ in sweep_voltages]
            total = settings['repeats'] * len(sweep_voltages)
            completed = 0
            for repeat in range(settings['repeats']):
                for index, voltage in enumerate(sweep_voltages):
                    if self.stop_requested:
                        raise InterruptedError('測定を停止しました')
                    await send(settings['sweep_channel'], voltage)
                    reading = instrument.query('print(smua.measure.i())').strip()
                    measurements[index].append(float(reading))
                    point_mean = sum(measurements[index]) / len(measurements[index])
                    point_variance = sum(
                        (value - point_mean) ** 2 for value in measurements[index]
                    ) / len(measurements[index])
                    self.point_updated.emit({
                        'index': index,
                        'count': len(sweep_voltages),
                        'voltage': sweep_voltages[index] - .300,
                        'mean': point_mean,
                        'variance': point_variance,
                        'std': point_variance ** .5,
                    })
                    completed += 1
                    self.progress.emit(
                        f'測定中: {completed}/{total} 点 (反復 {repeat + 1}/{settings["repeats"]})'
                    )

            means = [sum(samples) / len(samples) for samples in measurements]
            variances = [
                sum((value - mean) ** 2 for value in samples) / len(samples)
                for samples, mean in zip(measurements, means)
            ]
            stddevs = [variance ** .5 for variance in variances]
            return {
                'voltage': [value - .300 for value in sweep_voltages],
                'mean': means,
                'variance': variances,
                'std': stddevs,
                'repeats': settings['repeats'],
            }
        finally:
            if instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    pass
            if manager is not None:
                try:
                    manager.close()
                except Exception:
                    pass
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
