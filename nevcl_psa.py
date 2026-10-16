"""PSAスイーパーとNI-Scope設定、プロットの画面部品。

掃引条件や保存ボタンは SweepPanel、取得チャンネルや生波形は ScopePanel にある。
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
    """掃引条件、実行ボタン、PSA結果グラフをまとめた画面。"""
    def __init__(self,parent=None):
        # 入力値そのものを管理し、実行処理は親へシグナルで渡す。
        super().__init__(parent); root=QVBoxLayout(self); split=QSplitter(Qt.Orientation.Horizontal); root.addWidget(split,1)
        # 左側に掃引条件、右側にPSA結果プロットを並べる。
        ctrl=QWidget(); controls=QVBoxLayout(ctrl); cfg=QGroupBox('スイープ設定'); form=QFormLayout(cfg)
        self.mode=QComboBox(); self.mode.addItems(['NCMODE','BDMODE']); self.parameter=QComboBox(); self.start=_spin(0,100,.001); self.stop=_spin(0,100,.001); self.steps=QSpinBox(); self.steps.setRange(1,1000); self.steps.setValue(40)
        self.error_bar=QCheckBox('標準偏差をエラーバー表示')
        self.direction=QComboBox(); self.direction.addItems(['↓','↑','↓↑','↑↓']); self.initial_delay=_spin(0,100,.001); self.initial_delay.setValue(.5); self.period=_spin(.001,100,.001); self.period.setValue(.25); self.delay=_spin(0,100,.001); self.delay.setValue(.05); self.frequency=QComboBox(); self.frequency.addItem('50 kS/s',50000); self.frequency.addItem('500 kS/s',500000)
        for label,widget in [('モード',self.mode),('掃引パラメータ',self.parameter),('開始値 (V)',self.start),('終了値 (V)',self.stop),('ステップ数',self.steps),('方向',self.direction),('初期待機 (s)',self.initial_delay),('各点の計測時間 (s)',self.period),('点間待機 (s)',self.delay),('サンプリング',self.frequency)]:form.addRow(label,widget)
        form.addRow(self.error_bar)
        buttons=QHBoxLayout(); self.run_button=QPushButton('開始'); self.stop_button=QPushButton('停止'); self.stop_button.setEnabled(False); buttons.addWidget(self.run_button); buttons.addWidget(self.stop_button); form.addRow(buttons); controls.addWidget(cfg); controls.addStretch(1)
        plots=QWidget(); pv=QVBoxLayout(plots)
        # 表示／非表示とオフセットはプロットの近くで調整できるようにする。
        channel_group=QGroupBox('プロットするチャンネル'); channel_layout=QVBoxLayout(channel_group)
        self.channel_plot_table=QTableWidget(0,3); self.channel_plot_table.setHorizontalHeaderLabels(['Plot','チャンネル','Offset (V)']); self.channel_plot_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch); self.channel_plot_table.verticalHeader().hide(); self.channel_plot_table.setMaximumHeight(145); channel_layout.addWidget(self.channel_plot_table); pv.addWidget(channel_group)
        self.fig,self.ax,self.canvas,self.plot_toolbar=_plot(plots); self.ax.set_box_aspect(1); self.save_button=QPushButton('取得データをCSV保存'); pv.addWidget(self.save_button); split.addWidget(ctrl); split.addWidget(plots); split.setSizes([380,700])

class ScopePanel(QWidget):
    """NI-Scope union／チャンネル設定と最新波形表示の画面。"""
    def __init__(self,parent=None):
        super().__init__(parent); layout=QVBoxLayout(self); top=QHBoxLayout(); self.union=QComboBox(); top.addWidget(QLabel('NI-Scope union')); top.addWidget(self.union); self.apply_button=QPushButton('チャンネル設定を適用'); top.addWidget(self.apply_button); self.raw_toggle=QCheckBox('取得波形を表示'); top.addWidget(self.raw_toggle); layout.addLayout(top)
        self.channel_table=QTableWidget(0,6); self.channel_table.setHorizontalHeaderLabels(['使用','機器','CH','表示名','レンジ (V)','結合']); self.channel_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch); layout.addWidget(self.channel_table,1)
        self.fig,self.ax,self.canvas,self.plot_toolbar=_plot(self)
