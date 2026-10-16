"""パラメータ一覧とDAQmx機器一覧の画面部品。

パラメータの表示形式や列幅を変える場合は ParameterPanel を編集する。
値変更後の自動出力など、通信を伴う処理は親の NevClientWindow が担当する。
"""
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QGroupBox,QTableWidget,QHeaderView,QTabWidget,QAbstractItemView,QDoubleSpinBox,QTableWidgetItem
from PyQt6.QtCore import Qt

ATTRS=[('ID','MAN'),('COMMENT','OPT'),('DEV','MAN'),('CH','MAN'),('PIN','OPT'),('LABEL','MAN'),('NCMODE','MAN'),('BDMODE','OPT')]

class ParameterPanel(QWidget):
    """左側のセット一覧と、メインタブに置く詳細ページを管理する。"""
    def __init__(self,parent=None):
        # セット一覧の下に詳細タブを置く、元の左側レイアウトを保つ。
        super().__init__(parent); layout=QVBoxLayout(self)
        group=QGroupBox('パラメータセット'); box=QVBoxLayout(group)
        self.set_table=QTableWidget(0,2); self.set_table.setHorizontalHeaderLabels(['セット名','状態']); self.set_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch); box.addWidget(self.set_table); group.setMaximumHeight(145); layout.addWidget(group,0)
        self.detail_tabs=QTabWidget(); self.detail_tabs.setMinimumWidth(0); self.detail_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone); self.detail_tabs.tabBar().setExpanding(True); layout.addWidget(self.detail_tabs,1)
        self.attributes_page=QWidget(); attributes_layout=QVBoxLayout(self.attributes_page); attributes_layout.setContentsMargins(4,4,4,4)
        self.attr_table=QTableWidget(0,len(ATTRS)); self.attr_table.setHorizontalHeaderLabels([name for name,_ in ATTRS]); attributes_layout.addWidget(self.attr_table)
        attr_header=self.attr_table.horizontalHeader()
        # 属性表は列幅を均等に使い、狭い左ペインでも横スクロールを出さない。
        for col in range(len(ATTRS)): attr_header.setSectionResizeMode(col,QHeaderView.ResizeMode.Stretch)
        attr_header.setMinimumSectionSize(34)
        self.attributes_page.setMinimumWidth(0)
        self.parameters_page=QWidget(); parameters_layout=QVBoxLayout(self.parameters_page); parameters_layout.setContentsMargins(4,4,4,4)
        self.param_table=QTableWidget(0,6); self.param_table.setHorizontalHeaderLabels(['ID','parameter','(mV)','ID','parameter','(mV)']); self.param_table.verticalHeader().hide(); self.param_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel); parameters_layout.addWidget(self.param_table)
        self.value_widgets=[]
        self.detail_tabs.addTab(self.attributes_page,'属性'); self.detail_tabs.addTab(self.parameters_page,'選択セットのパラメータ')
        self.setStyleSheet('''
            QTableWidget { font-size: 9pt; gridline-color: #d5dbe2; }
            QTableWidget::item { padding: 0px 2px; }
            QHeaderView::section { font-size: 9pt; padding: 1px 3px; }
            QTabBar::tab { font-size: 9pt; padding: 3px 8px; }
            QGroupBox { font-size: 9pt; margin-top: 5px; padding-top: 4px; }
            QDoubleSpinBox { font-size: 9pt; min-height: 17px; padding: 0px; }
        ''')
        for table in (self.set_table,self.attr_table,self.param_table):
            table.verticalHeader().setDefaultSectionSize(19)
            table.verticalHeader().setMinimumSectionSize(17)
            table.verticalHeader().setVisible(False)
            table.setAlternatingRowColors(True)
            table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.param_table.setStyleSheet(self.param_table.styleSheet()+' QLabel { font-size: 9pt; }')

    def set_parameters(self,labels,ids,values,on_change):
        # パラメータを左右2ブロックに分け、ID・名前・数値スピン欄を横並びにする。
        self.value_widgets=[]
        count=len(labels); half=(count+1)//2
        self.param_table.clearContents(); self.param_table.setRowCount(half); self.param_table.setColumnCount(6)
        for index,(label,value) in enumerate(zip(labels,values)):
            side=0 if index<half else 1; row=index if side==0 else index-half; base=side*3
            id_item=QTableWidgetItem(str(ids[index] if index<len(ids) else '')); id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter); id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable); self.param_table.setItem(row,base,id_item)
            name=QTableWidgetItem(str(label)); name.setToolTip(str(label)); name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable); self.param_table.setItem(row,base+1,name)
            spin=QDoubleSpinBox(); spin.setRange(0,2.5); spin.setSingleStep(.0001); spin.setDecimals(4); spin.setKeyboardTracking(False)
            try:spin.setValue(float(value))
            except (TypeError,ValueError):spin.setValue(0)
            spin.valueChanged.connect(lambda val,i=index:on_change(i,val)); self.param_table.setCellWidget(row,base+2,spin); self.value_widgets.append(spin)
        for col,width in [(0,48),(2,112),(3,48),(5,112)]:
            self.param_table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.Fixed); self.param_table.setColumnWidth(col,width)
        for col in (1,4):self.param_table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.Stretch)
        for row in range(half):self.param_table.setRowHeight(row,21)

    def clear_parameters(self):
        # 選択セットがなくなったとき、値欄を空に戻す。
        self.value_widgets=[]; self.param_table.clearContents(); self.param_table.setRowCount(0); self.param_table.setColumnCount(6)

class DevicePanel(QWidget):
    """接続したDAQmx機器の一覧を表示するパネル。"""
    def __init__(self,parent=None):
        super().__init__(parent); layout=QVBoxLayout(self)
        self.device_table=QTableWidget(0,8); self.device_table.setHorizontalHeaderLabels(['種別','ID','機器名','モデル','CH数','長さ','周波数','状態']); layout.addWidget(self.device_table)
