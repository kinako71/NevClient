"""PSAスイーパーとPRCプロットの画面部品。

掃引条件と取得チャンネル設定は SweepPanel にある。
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

class PRCPanel(QWidget):
    """PRC表示用の空のプロット領域。"""
    def __init__(self,parent=None):
        super().__init__(parent); layout=QVBoxLayout(self)
        self.fig,self.ax,self.canvas,self.toolbar=_plot(self)


