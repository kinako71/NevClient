"""PSAスイーパーとPulse Stimulus、プロットの画面部品。

掃引条件と取得チャンネル設定は SweepPanel、刺激と結果表示は PulseStimulusPanel にある。
データ取得や再描画の処理本体は親の nevcl.py にある。
"""
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QSplitter,QGroupBox,QFormLayout,QPushButton,QTableWidget,QCheckBox,QComboBox,QLabel,QDoubleSpinBox,QSpinBox,QHeaderView
from PyQt6.QtCore import Qt

def _spin(lo,hi,step,dec=4):
    # 桁数と増減幅を指定して数値入力欄を作る。
    w=QDoubleSpinBox(); w.setRange(lo,hi); w.setSingleStep(step); w.setDecimals(dec); return w

def _plot(parent):
    # Matplotlibのグラフ、ナビゲーションツールバー、描画キャンバスを組み立てる。
    # 呼び出し側ですでにレイアウトを作っている場合はそれを使い、二重設定を避ける。
    layout=parent.layout()
    if layout is None: layout=QVBoxLayout(parent)
    fig=Figure(); canvas=FigureCanvasQTAgg(fig); ax=fig.add_subplot(111); ax.grid(True); toolbar=NavigationToolbar2QT(canvas,parent); layout.addWidget(canvas,1); layout.addWidget(toolbar); return fig,ax,canvas,toolbar

class SweepPanel(QWidget):
    """掃引条件と結果プロットを左右に配置した画面。"""
    def __init__(self,parent=None):
        # 元のParameter Sweeperと同じく、左側に設定、右側に表示と保存を置く。
        super().__init__(parent); root=QVBoxLayout(self); split=QSplitter(Qt.Orientation.Horizontal); root.addWidget(split,1)
        controls=QWidget(); layout=QVBoxLayout(controls); cfg=QGroupBox('スイープ設定'); form=QFormLayout(cfg)
        self.measurement_source=QComboBox(); self.measurement_source.addItem('NI-Scope','niscope'); self.measurement_source.addItem('NI-DAQmx AI','daqmx')
        form.addRow('計測方式',self.measurement_source)
        self.union=QComboBox(); self.union_label=QLabel('NI-Scope union'); form.addRow(self.union_label,self.union)
        self.mode=QComboBox(); self.mode.addItems(['NCMODE','BDMODE']); self.parameter=QComboBox(); self.start=_spin(0,100,.001); self.stop=_spin(0,100,.001); self.steps=QSpinBox(); self.steps.setRange(1,1000); self.steps.setValue(40)
        self.error_bar=QCheckBox('標準偏差をエラーバー表示')
        self.direction=QComboBox(); self.direction.addItems(['↓','↑','↓↑','↑↓']); self.initial_delay=_spin(0,100,.001); self.initial_delay.setValue(.5); self.period=_spin(.001,100,.001); self.period.setValue(.25); self.delay=_spin(0,100,.001); self.delay.setValue(.05); self.frequency=QComboBox(); self.frequency.addItem('50 kS/s',50000); self.frequency.addItem('500 kS/s',500000)
        for label,widget in [('モード',self.mode),('掃引パラメータ',self.parameter),('開始値 (V)',self.start),('終了値 (V)',self.stop),('ステップ数',self.steps),('方向',self.direction),('初期待機 (s)',self.initial_delay),('各点の計測時間 (s)',self.period),('点間待機 (s)',self.delay),('サンプリング',self.frequency)]:form.addRow(label,widget)
        form.addRow(self.error_bar)
        buttons=QHBoxLayout(); self.run_button=QPushButton('開始'); self.stop_button=QPushButton('停止'); self.stop_button.setEnabled(False); buttons.addWidget(self.run_button); buttons.addWidget(self.stop_button); form.addRow(buttons); layout.addWidget(cfg); layout.addStretch(1); split.addWidget(controls)
        plots=QWidget(); plot_layout=QVBoxLayout(plots)
        x_axis_row=QHBoxLayout(); x_axis_row.addWidget(QLabel('横軸'))
        self.x_axis=QComboBox(); self.x_axis.addItem('掃引パラメータ',-1); x_axis_row.addWidget(self.x_axis,1); plot_layout.addLayout(x_axis_row)
        channel_group=QGroupBox('NI-Scopeチャンネル（使用・Plot・Offset）'); self.channel_group=channel_group; channel_layout=QVBoxLayout(channel_group)
        self.channel_plot_table=QTableWidget(0,4); self.channel_plot_table.setHorizontalHeaderLabels(['使用','Plot','チャンネル','Offset (V)']); self.channel_plot_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch); self.channel_plot_table.verticalHeader().hide(); self.channel_plot_table.setMaximumHeight(150); channel_layout.addWidget(self.channel_plot_table); plot_layout.addWidget(channel_group)
        self.fig,self.ax,self.canvas,self.plot_toolbar=_plot(plots); self.ax.set_box_aspect(1)
        save_buttons=QHBoxLayout(); self.save_button=QPushButton('取得データをCSV保存'); self.save_image_button=QPushButton('プロット画像保存'); save_buttons.addWidget(self.save_button); save_buttons.addWidget(self.save_image_button); plot_layout.addLayout(save_buttons)
        split.addWidget(plots); split.setSizes([380,700])

class PulseStimulusPanel(QWidget):
    """上段に刺激波形、下段にPSA結果プロットを表示するタブ。"""
    def __init__(self,parent=None):
        super().__init__(parent); layout=QVBoxLayout(self); split=QSplitter(Qt.Orientation.Vertical); layout.addWidget(split,1)
        upper=QSplitter(Qt.Orientation.Horizontal); split.addWidget(upper)
        settings=QGroupBox('Pulse Stimulus'); settings_layout=QVBoxLayout(settings); form=QFormLayout()
        self.output=QComboBox(); form.addRow('出力チャンネル',self.output)
        self.dt=_spin(.001,10,.001,3); self.dt.setValue(.01); self.duration=_spin(.01,1000,.1,2); self.duration.setValue(20)
        timing=QHBoxLayout(); timing.addWidget(QLabel('dt (ms)')); timing.addWidget(self.dt); timing.addWidget(QLabel('T (ms)')); timing.addWidget(self.duration); form.addRow('時間設定',timing); settings_layout.addLayout(form)
        pulse_row=QHBoxLayout(); self.pulses=[]
        for index,(enabled,delay,width,amplitude) in enumerate(((True,5,1,100),(False,15,1,100)),1):
            group=QGroupBox(f'Pulse {index}'); pulse_form=QFormLayout(group); active=QCheckBox('有効'); active.setChecked(enabled)
            delay_spin=_spin(0,1000,.01,2); delay_spin.setValue(delay); width_spin=_spin(.01,1000,.01,2); width_spin.setValue(width); amp_spin=_spin(-1000,1000,1,1); amp_spin.setValue(amplitude)
            pulse_form.addRow(active); pulse_form.addRow('Delay (ms)',delay_spin); pulse_form.addRow('Width (ms)',width_spin); pulse_form.addRow('Amplitude (mV)',amp_spin); pulse_row.addWidget(group,1); self.pulses.append((active,delay_spin,width_spin,amp_spin))
        settings_layout.addLayout(pulse_row); self.fire_button=QPushButton('刺激を出力'); settings_layout.addWidget(self.fire_button); upper.addWidget(settings)
        stimulus_plot=QWidget(); stimulus_layout=QVBoxLayout(stimulus_plot); self.stim_fig,self.stim_ax,self.stim_canvas,self.stim_toolbar=_plot(stimulus_plot); self.stim_canvas.setMinimumHeight(160); upper.addWidget(stimulus_plot); upper.setSizes([440,650])
        results=QWidget(); results_layout=QVBoxLayout(results); self.fig,self.ax,self.canvas,self.plot_toolbar=_plot(results); self.ax.set_box_aspect(1)
        save_buttons=QHBoxLayout(); self.save_button=QPushButton('取得データをCSV保存'); self.save_image_button=QPushButton('プロット画像保存'); save_buttons.addWidget(self.save_button); save_buttons.addWidget(self.save_image_button); results_layout.addLayout(save_buttons); split.addWidget(results); split.setSizes([440,400])
        self.dt.valueChanged.connect(self.update_preview); self.duration.valueChanged.connect(self.update_preview)
        for active,delay_spin,width_spin,amp_spin in self.pulses:
            active.toggled.connect(self.update_preview); delay_spin.valueChanged.connect(self.update_preview); width_spin.valueChanged.connect(self.update_preview); amp_spin.valueChanged.connect(self.update_preview)
        self.update_preview()

    def set_outputs(self,devices):
        current=self.output.currentData(); self.output.clear()
        for device in devices:
            if not device.static:
                for channel in range(device.N_data):self.output.addItem(f'{device.name}/AO-{channel}',(device,channel))
        index=self.output.findData(current)
        if index>=0:self.output.setCurrentIndex(index)

    def update_preview(self,*_args):
        dt=self.dt.value(); duration=self.duration.value(); count=max(2,int(duration/dt)+1); time=[i*dt for i in range(count)]; values=[0.0]*count
        for active,delay,width,amplitude in self.pulses:
            if active.isChecked():
                start=int(delay.value()/dt); length=int(width.value()/dt)
                for i in range(start,min(count,start+length)):values[i]+=amplitude.value()
        self.waveform=values; self.stim_ax.clear(); self.stim_ax.grid(True); self.stim_ax.step(time,values,where='post')
        self.stim_ax.set_xlim(0,duration); self.stim_ax.set_xlabel('Time (ms)'); self.stim_ax.set_ylabel('Amplitude (mV)')
        low=min(values); high=max(values); margin=max((high-low)*.1,1); self.stim_ax.set_ylim(low-margin,high+margin); self.stim_canvas.draw_idle()

class PRCPanel(QWidget):
    """PRC表示用の空のプロット領域。"""
    def __init__(self,parent=None):
        super().__init__(parent); layout=QVBoxLayout(self)
        self.fig,self.ax,self.canvas,self.toolbar=_plot(self)


