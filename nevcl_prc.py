"""DAQmxを使ったPRC計測画面とサーバー通信。"""
import re
import time

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox, QFormLayout,
    QPushButton, QComboBox, QLabel, QDoubleSpinBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QFileDialog, QCheckBox,
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
        self.pulse_width.setValue(1)
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
        self.show_error_bars = QCheckBox('エラーバーを表示')
        self.show_error_bars.setToolTip('結果に誤差情報が含まれる場合に表示します')
        results_layout.addWidget(self.show_error_bars)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['位相 (ms)', 'PRC値'])
        self.table.setMinimumHeight(150)
        results_layout.addWidget(self.table, 2)
        save_row = QHBoxLayout()
        self.save_button = QPushButton('結果をCSV保存…')
        self.save_button.setEnabled(False)
        self.save_image_button = QPushButton('プロットを画像保存…')
        self.save_image_button.setEnabled(False)
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.save_image_button)
        save_row.addStretch(1)
        results_layout.addLayout(save_row)
        split.addWidget(results)
        split.setSizes([360, 850])
        self.result_rows = []
        self.show_error_bars.toggled.connect(self._redraw_results)
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
        """位相とPRC値を表とPRC曲線に反映する。"""
        self.result_rows = rows
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row[:2]):
                self.table.setItem(row_index, column, QTableWidgetItem(f'{value:.9g}'))
        self._redraw_results()
        self.save_button.setEnabled(bool(rows))
        self.save_image_button.setEnabled(bool(rows))
        self.status.setText(f'完了: {len(rows)}位相点')

    def _redraw_results(self, *_args):
        """誤差情報がある場合だけ、選択に応じてエラーバーを描く。"""
        self.ax.clear()
        self.ax.grid(True)
        rows = self.result_rows
        if rows:
            phases = [row[0] for row in rows]
            values = [row[1] for row in rows]
            if self.show_error_bars.isChecked() and all(len(row) >= 3 for row in rows):
                self.ax.errorbar(phases, values, yerr=[abs(row[2]) for row in rows], marker='o', capsize=3)
            else:
                self.ax.plot(phases, values, marker='o')
        self.ax.set_xlabel('Phase (ms)')
        self.ax.set_ylabel('PRC value')
        self.fig.tight_layout()
        self.canvas.draw_idle()


class PRCMeasurementJob(QObject):
    """SET/RUN/GET PRCを順に送り、結果6列を取り出す。"""
    done = pyqtSignal(object, object)
    progress = pyqtSignal(str)
    points_updated = pyqtSignal(object)

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
        payload = command.rstrip('\r\n') + '\n'
        print(f'[PRC] -> {payload!r}', flush=True)
        self.tcp.send(payload)
        self.progress.emit(f'サーバー応答待ち: {command}')
        response = self.tcp.recv(timeout=timeout)
        self._print_response(command, response)
        if response is None:
            raise RuntimeError(f'{command}に対する応答がありません')
        if any(line.lstrip().startswith('!') for line in response):
            raise RuntimeError(f'{command}が拒否されました: {response}')

    @staticmethod
    def _print_response(command, response):
        if response is None:
            print(f'[PRC] <- {command}: None', flush=True)
        else:
            print(f'[PRC] <- {command}: {response!r} [#OK]', flush=True)

    def measure(self):
        self._send_and_wait_ok(f'SET PRC {self.device.taskNo} {self.channel}')
        self._send_and_wait_ok(
            f'{self.max_phase:.12g} {self.phase_step:.12g} {self.repeats}'
        )
        self._send_and_wait_ok(
            f'{self.threshold:.12g} {self.pulse_width:.12g} {self.pulse_amplitude:.12g}'
        )
        self._send_and_wait_ok('RUN PRC')
        expected_points = int(self.max_phase // self.phase_step) + 1
        self.progress.emit('GET PRC STAT: 完了状態を確認しています…')
        deadline = time.monotonic() + 1800
        readable_points = 0
        rows = []
        while time.monotonic() < deadline:
            print("[PRC] -> 'GET PRC STAT\\n'", flush=True)
            self.tcp.send('GET PRC STAT\n')
            response = self.tcp.recv(timeout=30)
            self._print_response('GET PRC STAT', response)
            if not response:
                raise RuntimeError('GET PRC STATの応答がありません')
            stat_index = next(
                (index for index, line in enumerate(response)
                 if re.search(r'#PRCSTAT\b', line, re.IGNORECASE)),
                None,
            )
            if stat_index is None:
                raise RuntimeError(f'GET PRC STAT応答の形式が不正です: {response[:3]}')
            stat_line = response[stat_index].strip()
            body = re.sub(
                r'^#(?:(?:GET)?PRCSTAT|PSASTAT)\b', '', stat_line,
                flags=re.IGNORECASE,
            ).strip()
            if not body and stat_index + 1 < len(response):
                body = response[stat_index + 1].strip()
                stat_line = f'{stat_line} {body}'
            fields = body.split()
            numbers = []
            for field in fields:
                if re.fullmatch(r'[-+]?\d+', field):
                    numbers.append(int(field))
            if not numbers:
                raise RuntimeError(f'GET PRC STATからポイント数を読めません: {stat_line}')
            # #PRCSTATの次行は「読取可能点数 現在位相 状態 [エラー]」。
            readable_points = numbers[0]
            try:
                current_phase = float(fields[1])
            except (IndexError, ValueError):
                current_phase = 0.0
            state = next(
                (field.upper() for field in fields
                 if re.search(r'[A-Za-z]', field)),
                '',
            )
            self.progress.emit(
                f'PRC計測中: {state or "状態確認中"} / '
                f'{readable_points}/{expected_points}点取得可能 '
                f'(位相 {current_phase:g} ms)'
            )
            # STATで新しいポイントを確認したら、その範囲だけ取得して画面へ渡す。
            if readable_points > len(rows):
                new_rows = self._get_data(len(rows))
                if new_rows:
                    rows.extend(new_rows)
                    self.points_updated.emit(list(rows))
            if state in {'ERROR', 'FAILED', 'ABORTED'}:
                raise RuntimeError(f'PRC計測が失敗しました: {stat_line}')
            if state == 'UNKNOWN':
                raise RuntimeError(f'サーバーがPRC状態を判定できません: {stat_line}')
            if state == 'COMPLETE':
                break
            if state in {'IDLE', 'CONFIGURED'}:
                if readable_points >= expected_points:
                    break
                raise RuntimeError(f'PRC計測が完了状態になりましたが、点数が不足しています: {stat_line}')
            if not state and readable_points >= expected_points:
                break
            time.sleep(.25)
        else:
            raise TimeoutError('PRC計測が30分以内に完了しませんでした')

        if readable_points > len(rows):
            self.progress.emit(f'GET PRC DATA {len(rows)}-: 残りを取得中…')
            rows.extend(self._get_data(len(rows), timeout=120))
            self.points_updated.emit(list(rows))
        return rows

    def _get_data(self, start, timeout=30):
        """指定した最初のポイント以降を取得し、位相とPRC値の組にする。"""
        command = f'GET PRC DATA {start}-'
        print(f'[PRC] -> {command!r}\\n', flush=True)
        self.tcp.send(command + '\n')
        response = self.tcp.recv(timeout=timeout)
        self._print_response(command, response)
        if not response:
            raise RuntimeError('GET PRC DATAの応答がありません')
        data_index = next(
            (index for index, line in enumerate(response)
             if re.search(r'#PRCDATA\b', line, re.IGNORECASE)),
            None,
        )
        if data_index is None:
            raise RuntimeError(f'GET PRC DATA応答に#PRCDATAタグがありません: {response[:3]}')
        data_header = response[data_index].strip().split()
        requested_count = None
        if len(data_header) >= 3:
            try:
                first_point, last_point = int(data_header[1]), int(data_header[2])
                if last_point >= first_point:
                    requested_count = last_point - first_point + 1
            except ValueError:
                pass
        rows = []
        pending_phase = None
        for line in response[data_index + 1:]:
            content = line.strip()
            if not content or content.startswith('#'):
                continue
            # PRCDATAは位相のスカラー行と[PRC値]の行が交互に返る。
            values = re.findall(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', content)
            if not values:
                continue
            try:
                parsed = [float(value) for value in values]
            except ValueError:
                continue
            is_bracketed_value = content.startswith('[') and content.endswith(']')
            if is_bracketed_value:
                if pending_phase is not None:
                    rows.append((pending_phase, parsed[0]))
                    pending_phase = None
                continue
            if len(parsed) >= 2:
                rows.append((parsed[0], parsed[1]))
            elif pending_phase is None:
                pending_phase = parsed[0]
            else:
                rows.append((pending_phase, parsed[0]))
                pending_phase = None
        if not rows and requested_count:
            raise RuntimeError(f'GET PRC DATA応答に2列の結果行がありません: {response[:3]}')
        if requested_count is not None and len(rows) != requested_count:
            raise RuntimeError(
                f'#PRCDATAは{requested_count}点の指定ですが、{len(rows)}点を読み取りました'
            )
        return rows

