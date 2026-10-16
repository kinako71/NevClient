#!/usr/bin/env python3
"""PyQt6版NEVクライアントの起動処理と画面間の連携をまとめたファイル。

画面部品は nevcl_param.py / nevcl_psa.py、CSVの読み書きは nevcl_csvif.py、
機器通信は nevcl_tcp.py / nevcl_daqmx.py / nevcl_niscope.py に分けている。
画面配置や操作の流れを変えるときは主にこのファイルを編集する。
"""
import csv, sys
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import Qt, QTimer, QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QGroupBox, QFormLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QLabel, QTabWidget, QDoubleSpinBox, QComboBox,
    QPlainTextEdit, QHeaderView, QCheckBox, QSpinBox, QLineEdit)
import nevcl_tcp, nevcl_daqmx, nevcl_niscope
from nevcl_csvif import CsvParameterFile
from nevcl_param import ATTRS, ParameterPanel, DevicePanel
from nevcl_psa import SweepPanel, ScopePanel

ATTRS=list(ATTRS)

class Job(QObject):
    """接続など待ち時間のある処理をGUIスレッド外で実行する小さなジョブ。"""
    done=pyqtSignal(object,object)
    def __init__(self, fn): super().__init__(); self.fn=fn
    def run(self):
        try:self.done.emit(self.fn(),None)
        except Exception as e:self.done.emit(None,e)

class NevClientWindow(QMainWindow):
    """アプリの状態を保持し、画面パネルと計測通信をつなぐメイン画面。"""
    def __init__(self,address='localhost'):
        # CSV、機器接続、PSAの状態を初期化して画面を組み立てる。
        super().__init__(); self.setWindowTitle('NEV Client — PyQt6'); self.resize(1450,950)
        self.address=address; self.tcp=None; self.daq=None; self.scope=None; self.csv_model=None; self.csv_path=None; self.raw=[]; self.cols={}; self.pset_cols=[]; self.active_set=None
        self.sweep_active=False; self.sweep_bookmark=0; self.sweep_dirs=[]; self.psa_data=[]; self.channel_rows=[]; self.scope_channels=[]; self.sweep_channel_info=[]; self.sweep_data=[]; self.sweep_param_values=[]
        self._build_ui(); self.timer=QTimer(self); self.timer.setInterval(400); self.timer.timeout.connect(self.poll_sweep)
        self.statusBar().showMessage(f'{address}:9000 | 未接続')

    def _build_ui(self):
        # 上部の操作ボタン、左側のパラメータ、右側の機器／PSAタブを配置する。
        root=QWidget(); self.setCentralWidget(root); outer=QVBoxLayout(root)
        bar=QHBoxLayout()
        for title,fn in [('接続',self.connect_server),('CSVを開く',self.open_csv),('CSV保存',self.save_csv),('セット追加',self.add_set),('セット削除',self.delete_set),('出力更新',self.drive_outputs),('ゼロクリア',self.zero_clear)]:
            b=QPushButton(title); b.clicked.connect(fn); bar.addWidget(b)
        self.auto_update=QCheckBox('値変更時に自動出力'); self.auto_update.setToolTip('有効にすると、スピンボタンで値を変えるたびにDAQmx出力を更新します'); bar.addWidget(self.auto_update)
        self.path_label=QLabel('CSV: (未選択)'); bar.addWidget(self.path_label,1); outer.addLayout(bar)
        split=QSplitter(Qt.Orientation.Horizontal); outer.addWidget(split,1)
        self.parameter_panel=ParameterPanel(); self.set_table=self.parameter_panel.set_table; self.attr_table=self.parameter_panel.attr_table; self.param_table=self.parameter_panel.param_table; self.set_table.cellClicked.connect(self.select_set); split.addWidget(self.parameter_panel)
        self.tabs=QTabWidget(); split.addWidget(self.tabs); split.setSizes([570,880])
        self.sweep_panel=SweepPanel(); self.fig=self.sweep_panel.fig; self.ax=self.sweep_panel.ax; self.canvas=self.sweep_panel.canvas
        self.sweep_mode=self.sweep_panel.mode; self.sweep_param=self.sweep_panel.parameter; self.sweep_start=self.sweep_panel.start; self.sweep_stop=self.sweep_panel.stop; self.sweep_steps=self.sweep_panel.steps; self.sweep_dir=self.sweep_panel.direction; self.idelay=self.sweep_panel.initial_delay; self.period=self.sweep_panel.period; self.delay=self.sweep_panel.delay; self.frequency=self.sweep_panel.frequency; self.override_table=self.sweep_panel.override_table; self.run_btn=self.sweep_panel.run_button; self.stop_btn=self.sweep_panel.stop_button
        self.error_bar=self.sweep_panel.error_bar; self.run_btn.clicked.connect(self.start_sweep); self.stop_btn.clicked.connect(self.stop_sweep); self.sweep_panel.save_button.clicked.connect(self.save_psa_data); self.sweep_mode.currentTextChanged.connect(self._select_sweep_mode); self.tabs.addTab(self.sweep_panel,'Parameter Sweeper')
        self.scope_panel=ScopePanel(); self.union_combo=self.scope_panel.union; self.scope_table=self.scope_panel.channel_table; self.raw_toggle=self.scope_panel.raw_toggle; self.fig_raw=self.scope_panel.fig; self.ax_raw=self.scope_panel.ax; self.canvas_raw=self.scope_panel.canvas; self.scope_panel.apply_button.clicked.connect(self.apply_scope); self.raw_toggle.toggled.connect(lambda _checked:self._update_plots()); self.tabs.addTab(self.scope_panel,'NI-Scope / Waveform')
        self.device_panel=DevicePanel(); self.device_table=self.device_panel.device_table; self.tabs.addTab(self.device_panel,'DAQmx Devices')
        self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.tabs.addTab(self.log,'通信ログ')

    def _start_job(self,fn,callback):
        # ブロッキングする接続処理を別スレッドで動かし、完了後に画面へ戻す。
        self.run_btn.setEnabled(False); self.thread=QThread(); self.job=Job(fn); self.job.moveToThread(self.thread); self.thread.started.connect(self.job.run); self.job.done.connect(callback); self.job.done.connect(self.thread.quit); self.thread.finished.connect(self.thread.deleteLater); self.thread.start()
    def _job_result(self,result,error):
        self.run_btn.setEnabled(True)
        if error:self._error(str(error)); return
        self.log.appendPlainText(str(result))
    def _error(self,message): QMessageBox.critical(self,'操作エラー',message); self.log.appendPlainText('ERROR: '+message)
    def _log(self,message): self.log.appendPlainText(str(message))

    def connect_server(self):
        # localhost:9000（または起動引数のホスト）へ接続して機器情報を読む。
        self.statusBar().showMessage('接続中…')
        def connect():
            import nevcl_sys
            self.tcp=nevcl_tcp.nevcl_tcpCon(self.address); self.daq=nevcl_daqmx.nevcl_sysDAQmx(self.tcp); self.scope=nevcl_niscope.nevcl_sysNIScope(self.tcp)
            nevcl_daqmx.sys=self.daq; nevcl_niscope.sys=self.scope
            return 'DAQmx '+str(sum(map(len,[self.daq.saos,self.daq.daos,self.daq.sdos,self.daq.ddos])))+'台 / NI-Scope '+str(self.scope.N_devices)+'台 / PSA unions '+str(self.scope.N_unions)+'個'
        def done(res,err):
            self.run_btn.setEnabled(True)
            if err:self.statusBar().showMessage('接続に失敗しました'); self._error(str(err)); return
            self.statusBar().showMessage(self.address+' に接続しました'); self._log(res); self._refresh_devices(); self._refresh_scope_channels()
        self._start_job(connect,done)

    def _refresh_devices(self):
        # DAQmx機器一覧と、選択可能なNI-Scope unionを画面に反映する。
        if not self.daq:return
        devs=[]
        for typ,seq in [('SAO',self.daq.saos),('DAO',self.daq.daos),('SDO',self.daq.sdos),('DDO',self.daq.ddos)]:
            for d in seq:devs.append([typ,d.id,d.name,d.model,d.N_data,d.L_data,d.freq,d.state])
        self.device_table.setRowCount(len(devs))
        for r,row in enumerate(devs):
            for c,val in enumerate(row):self.device_table.setItem(r,c,QTableWidgetItem(str(val)))
        self.union_combo.clear()
        if self.scope:
            for i in range(self.scope.N_unions):self.union_combo.addItem(f'Union {i}',i)

    def _refresh_scope_channels(self):
        # 接続済みNI-Scope機器の全チャンネルを設定表へ並べる。
        if not self.scope:return
        self.scope_channels=[]
        for di,d in enumerate(self.scope.device_list):
            for ch in range(d.N_channels):self.scope_channels.append((di,d,ch))
        self.scope_table.setRowCount(len(self.scope_channels))
        for r,(di,d,ch) in enumerate(self.scope_channels):
            active=QCheckBox(); active.setChecked(True); self.scope_table.setCellWidget(r,0,active)
            values=[d.name,str(ch),f'{d.name}/CH{ch}','10','DC']
            for c,val in enumerate(values,1):self.scope_table.setItem(r,c,QTableWidgetItem(val))
            self.scope_table.item(r,5).setText('DC')
            # coupling selector is editable via a combo box
            combo=QComboBox(); combo.addItems(['DC','AC','GND']); combo.setCurrentText('DC'); self.scope_table.setCellWidget(r,5,combo)
            self.scope_table.item(r,4).setText('2')

    def apply_scope(self):
        # チャンネル表の有効状態・レンジ・結合をサーバー側unionへ送る。
        if not self.scope:return self._error('先にサーバーへ接続してください')
        union=self.union_combo.currentData()
        if union is None:return self._error('利用可能なunionがありません')
        devs=[]; chconfs=[]
        for r,(di,d,ch) in enumerate(self.scope_channels):
            if self.scope_table.cellWidget(r,0).isChecked() and di not in devs:devs.append(di)
        if not devs:return self._error('1つ以上のNI-Scope機器を選択してください')
        try:
            self.scope.set_union_devlist(union,devs)
            for di in devs:
                conf=[]
                for r,(didx,d,ch) in enumerate(self.scope_channels):
                    if didx!=di:continue
                    if self.scope_table.cellWidget(r,0).isChecked():
                        conf.append([float(self.scope_table.item(r,4).text()),self.scope_table.cellWidget(r,5).currentText()])
                    else:conf.append(['*','*'])
                self.scope.set_union_channelConf(union,di,0,conf)
            self._log(f'Union {union}: devices {devs}, channel config applied')
        except Exception as e:self._error(str(e))

    def open_csv(self):
        # CSVを検証し、属性・パラメータセット・モード設定を各画面へ反映する。
        p,_=QFileDialog.getOpenFileName(self,'CSVを開く','','CSV (*.csv)')
        if not p:return
        try:
            self.csv_model=CsvParameterFile.read(p); self.raw=self.csv_model.rows; self.cols=self.csv_model.columns; self.pset_cols=self.csv_model.parameter_columns
            self.csv_path=Path(p); self.path_label.setText('CSV: '+self.csv_path.name); self._render_sets(); self._load_attributes()
            if self.pset_cols:self.select_set(0,0)
            self.statusBar().showMessage(f'CSV読み込み: {len(self.raw)-1} parameters')
        except Exception as e:self._error(str(e))
    def _render_sets(self):
        # セット一覧とNCMODE/BDMODEの掃引範囲・固定値を作り直す。
        self.set_table.setRowCount(len(self.pset_cols))
        for r,c in enumerate(self.pset_cols):self.set_table.setItem(r,0,QTableWidgetItem(self.raw[0][c])); self.set_table.setItem(r,1,QTableWidgetItem(''))
        names=self._labels(); self.sweep_param.clear(); self.sweep_param.addItems(names)
        self.override_table.setRowCount(0)
        self.scan_configs = {}
        for mode,key in [('NCMODE','NCMODE'),('BDMODE','BDMODE')]:
            col=self.cols.get(key)
            if col is None:continue
            for i,row in enumerate(self.raw[1:]):
                val=row[col] if col<len(row) else ''
                if not val:continue
                if val.startswith('#'):
                    try:
                        conf=eval(val[1:],{'__builtins__':{}},{})
                        if len(conf)>=3:
                            self.scan_configs[mode]=(i,conf)
                    except Exception:pass
                else:
                    r=self.override_table.rowCount(); self.override_table.insertRow(r); self.override_table.setItem(r,0,QTableWidgetItem(mode)); self.override_table.setItem(r,1,QTableWidgetItem(self._labels()[i] if i<len(self._labels()) else str(i))); self.override_table.setItem(r,2,QTableWidgetItem(val))
        self._select_sweep_mode(self.sweep_mode.currentText())
    def _select_sweep_mode(self,mode):
        # 選択したモードにCSVで定義された掃引対象と範囲を読み込む。
        conf=self.scan_configs.get(mode)
        if conf:
            idx,vals=conf; self.sweep_attribute_index=idx
            self.sweep_param.setCurrentIndex(idx)
            self.sweep_start.setValue(float(vals[0])); self.sweep_stop.setValue(float(vals[1])); self.sweep_steps.setValue(int(vals[2]))
        else:self.sweep_attribute_index=None
    def _labels(self):
        i=self.cols.get('LABEL'); return [r[i] for r in self.raw[1:] if i is not None and i<len(r)]
    def _load_attributes(self):
        self.attr_table.setRowCount(len(self.raw)-1)
        for r,row in enumerate(self.raw[1:]):
            for j,(name,_) in enumerate(ATTRS):
                c=self.cols.get(name,-1); self.attr_table.setItem(r,j,QTableWidgetItem(row[c] if 0<=c<len(row) else ''))
    def select_set(self,r,c=0):
        # セットの値を2列の一覧へ表示する。各値欄は矢印で増減できる。
        if r<0 or r>=len(self.pset_cols):return
        self.active_set=r; pc=self.pset_cols[r]; labels=self._labels(); ids=[]; values=[]
        for i,row in enumerate(self.raw[1:]):values.append(row[pc] if pc<len(row) else 0)
        labels=[labels[i] if i<len(labels) else f'Parameter {i+1}' for i in range(len(values))]
        idcol=self.cols.get('ID'); ids=[row[idcol] if idcol is not None and idcol<len(row) else '' for row in self.raw[1:]]
        self.parameter_panel.set_parameters(labels,ids,values,self.parameter_value_changed)
    def _parameter_widget(self,index):
        widgets=self.parameter_panel.value_widgets
        return widgets[index] if index<len(widgets) else None
    def parameter_value_changed(self,index,value):
        # 変更値をCSVモデルへ保持し、自動更新が有効ならすぐDAQmxへ送る。
        if self.active_set is None:return
        col=self.pset_cols[self.active_set]; row=self.raw[index+1]
        while len(row)<=col:row.append('')
        row[col]=f'{value:.4f}'
        if self.auto_update.isChecked():self.drive_outputs()
        else:self.statusBar().showMessage('値を変更しました。DAQmxへ反映するには「出力更新」を押してください。')
    def save_csv(self):
        # 画面上の最新値をCSVへ戻して保存する。
        if not self.raw:return self._error('先にCSVを読み込んでください')
        for r,c in enumerate(self.pset_cols):self.raw[0][c]=self.set_table.item(r,0).text()
        if self.active_set is not None:
            c=self.pset_cols[self.active_set]
            for r in range(len(self.raw)-1):
                while len(self.raw[r+1])<=c:self.raw[r+1].append('')
                widget=self._parameter_widget(r); self.raw[r+1][c]=f'{widget.value():.4f}' if widget else ''
        p=str(self.csv_path) if self.csv_path else QFileDialog.getSaveFileName(self,'CSV保存','','CSV (*.csv)')[0]
        if p:
            self.csv_model.rows=self.raw; self.csv_model.parameter_columns=self.pset_cols; self.csv_model.save(p)
            self.csv_path=Path(p); self.path_label.setText('CSV: '+self.csv_path.name)
    def add_set(self):
        if not self.raw:return self._error('先にCSVを読み込んでください')
        from PyQt6.QtWidgets import QInputDialog
        name,ok=QInputDialog.getText(self,'セット追加','セット名')
        if ok and name:
            try:self.csv_model.add_set(name)
            except ValueError as e:return self._error(str(e))
            self.raw=self.csv_model.rows; self.pset_cols=self.csv_model.parameter_columns; self._render_sets(); self.select_set(len(self.pset_cols)-1)
    def delete_set(self):
        r=self.set_table.currentRow()
        if r<0:return
        c=self.pset_cols[r]
        if QMessageBox.question(self,'確認',f'「{self.raw[0][c]}」を削除しますか？')!=QMessageBox.StandardButton.Yes:return
        self.csv_model.delete_set(r); self.raw=self.csv_model.rows; self.pset_cols=self.csv_model.parameter_columns; self.active_set=None; self._render_sets(); self.parameter_panel.clear_parameters()

    def drive_outputs(self):
        # 選択セットの全パラメータ値をDAQmx出力へ反映する。
        if not self.daq or not self.raw or self.active_set is None:return self._error('接続してCSVセットを選択してください')
        try:
            pcol=self.pset_cols[self.active_set]; labelcol=self.cols['LABEL']; devcol=self.cols['DEV']; chcol=self.cols['CH']
            for r,row in enumerate(self.raw[1:]):
                value_widget=self._parameter_widget(r) if r<len(self.raw)-1 else None
                if value_widget:
                    while len(row)<=pcol: row.append('')
                    row[pcol]=f'{value_widget.value():.4f}'
            for row in self.raw[1:]:
                if len(row)<=max(pcol,labelcol,devcol,chcol) or not row[labelcol]:continue
                io,ch=row[chcol].split('-',1); dev=self.daq.get_devinfo(row[devcol],io)
                if dev is None:raise ValueError('DAQmx channel not found: '+row[devcol]+'/'+row[chcol])
                self.daq.set_ch(dev,int(ch),[float(row[pcol])])
            ret=self.daq.update(); self._log('DAQmx outputs updated: '+str(ret)); self._refresh_devices()
        except Exception as e:self._error(str(e))
    def zero_clear(self):
        # DAQmx出力をゼロクリアする。
        if not self.daq:return self._error('先に接続してください')
        try:self.daq.zeroclear(); self._log('DAQmx zero clear completed')
        except Exception as e:self._error(str(e))

    def start_sweep(self):
        # 掃引・NI-Scope設定をサーバーへ送り、初期待機後にPSAを開始する。
        if not self.scope or not self.daq or not self.raw:return self._error('接続し、パラメータCSVを読み込んでください')
        try:
            if self.active_set is not None:
                pcol=self.pset_cols[self.active_set]
                for r,row in enumerate(self.raw[1:]):
                    value_widget=self._parameter_widget(r) if r<len(self.raw)-1 else None
                    if value_widget:
                        while len(row)<=pcol:row.append('')
                        row[pcol]=f'{value_widget.value():.4f}'
            union=self.union_combo.currentData(); i=self.sweep_attribute_index if self.sweep_attribute_index is not None else self.sweep_param.currentIndex(); label=self._labels()[i]; row=self.raw[i+1]; dev=self.daq.get_devinfo(row[self.cols['DEV']],row[self.cols['CH']].split('-',1)[0]); ch=int(row[self.cols['CH']].split('-',1)[1])
            if dev is None or dev.static: raise ValueError('スイープ対象には接続済みの動的DAQmxアナログ出力が必要です')
            dirs=[[1],[-1],[1,-1],[-1,1]][self.sweep_dir.currentIndex()]; self.sweep_dirs=dirs[1:]; direction=dirs[0]
            scope_devs=[]
            for r,(di,d,c) in enumerate(self.scope_channels):
                if self.scope_table.cellWidget(r,0).isChecked() and di not in scope_devs:scope_devs.append(di)
            if not scope_devs:raise ValueError('NI-Scope channelを1つ以上有効にしてください')
            self.scope.set_union_devlist(union,scope_devs)
            nchan=0
            channel_info=[]
            for di in scope_devs:
                devscope=self.scope.device_list[di]; conf=[]
                for r,(didx,d,c) in enumerate(self.scope_channels):
                    if didx!=di:continue
                    active=self.scope_table.cellWidget(r,0).isChecked()
                    if active:conf.append([float(self.scope_table.item(r,4).text()),self.scope_table.cellWidget(r,5).currentText()])
                    else:conf.append(['*','*'])
                    channel_info.append({'name':self.scope_table.item(r,3).text(),'active':active,'device':d,'channel':c,'range':self.scope_table.item(r,4).text(),'coupling':self.scope_table.cellWidget(r,5).currentText()})
                self.scope.set_union_channelConf(union,di,0,conf)
                nchan+=devscope.N_channels
            freq=self.frequency.currentData(); dlen=int((self.period.value()+self.delay.value())*freq)
            self.scope.set_union_dlen(union,dlen); self.scope.set_union_freq(union,freq)
            stop=self.sweep_stop.value() if direction>0 else self.sweep_start.value(); start=self.sweep_start.value() if direction>0 else self.sweep_stop.value()
            self.scope.set_psa(union,[dev.taskType,dev.taskNo,ch],[start,stop,self.sweep_steps.value()],int(self.delay.value()*freq))
            self.daq.set_ch(dev,ch,[start])
            selected_mode=self.sweep_mode.currentText()
            for r in range(self.override_table.rowCount()):
                if self.override_table.item(r,0).text()!=selected_mode:continue
                ovlabel=self.override_table.item(r,1).text(); val=float(self.override_table.item(r,2).text()); oi=self._labels().index(ovlabel); orow=self.raw[oi+1]; od=self.daq.get_devinfo(orow[self.cols['DEV']],orow[self.cols['CH']].split('-',1)[0]); och=int(orow[self.cols['CH']].split('-',1)[1]); self.daq.set_ch(od,och,[val])
            self.daq.update(); self.sweep_union=union; self.sweep_label=label; self.sweep_operation=self.sweep_mode.currentText(); self.sweep_bookmark=0; self.sweep_nchan=nchan; self.sweep_freq=freq; self.sweep_channel_info=channel_info; self.sweep_data=[[] for _ in range(nchan)]; self.sweep_param_values=[]
            self.sweep_active=True; self.run_btn.setEnabled(False); self.stop_btn.setEnabled(True)
            QTimer.singleShot(int(self.idelay.value()*1000),self._launch_sweep)
        except Exception as e:self._error(str(e))
    def _launch_sweep(self):
        if not self.sweep_active:return
        try:self.scope.run_psa(); self.timer.start(); self._log('PSA started: '+self.sweep_label)
        except Exception as e:self.stop_sweep(); self._error(str(e))
    def stop_sweep(self):
        if not self.sweep_active:return
        try:self.scope.stop_psa()
        except Exception as e:self._log('STOP PSA error: '+str(e))
        self.timer.stop(); self.sweep_active=False; self.run_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        try:self.drive_outputs()
        except Exception:pass
        self._log('PSA stopped; configured DAQmx values restored')
    def poll_sweep(self):
        # 定期呼び出し。PSA状態を確認し、新しい波形データだけ取り込む。
        if not self.sweep_active:return
        try:
            status=self.scope.get_psa_stat()
            if status is None:return
            points,pvalue,state=status
            if points>self.sweep_bookmark:
                last,data=self.scope.get_psa_data(self.sweep_bookmark,None)
                if data:
                    for param,channels in data:
                        self.sweep_param_values.append(param)
                        for i,segment in enumerate(channels):
                            if i<len(self.sweep_data):self.sweep_data[i].append(segment)
                self.sweep_bookmark=last+1; self.statusBar().showMessage(f'PSA {state}: {self.sweep_bookmark} points, current {pvalue:g} V'); self._update_plots()
            if state!='RUNNING':
                if self.sweep_dirs:
                    direction=self.sweep_dirs.pop(0); self.sweep_bookmark=0; start=self.sweep_start.value() if direction>0 else self.sweep_stop.value(); end=self.sweep_stop.value() if direction>0 else self.sweep_start.value(); idx=self._labels().index(self.sweep_label); row=self.raw[idx+1]; dev=self.daq.get_devinfo(row[self.cols['DEV']],row[self.cols['CH']].split('-',1)[0]); ch=int(row[self.cols['CH']].split('-',1)[1]); self.scope.set_psa(self.sweep_union,[dev.taskType,dev.taskNo,ch],[start,end,self.sweep_steps.value()],int(self.delay.value()*self.sweep_freq)); self.scope.run_psa()
                else:self.stop_sweep()
        except Exception as e:self.stop_sweep(); self._error(str(e))
    def _update_plots(self):
        # 取り込んだデータから掃引グラフと最新波形を再描画する。
        self.ax.clear(); self.ax.grid(True)
        colors=['#ff0000','#0000ff','#00aa00','#ff00ff','#cc9900','#00aaaa','#883355','#335588']
        for i,series in enumerate(self.sweep_data):
            info=self.sweep_channel_info[i] if i<len(self.sweep_channel_info) else {'active':True,'name':f'CH{i}'}
            if not info['active']:continue
            color=colors[i%len(colors)]; name=info['name']
            if self.sweep_operation=='BDMODE':
                low=[min(seg) if seg else 0 for seg in series]; high=[max(seg) if seg else 0 for seg in series]
                x=self.sweep_param_values[:len(series)]; self.ax.plot(x,low,label=f'{name} min',color=color); self.ax.plot(x,high,label=f'{name} max',color=color)
            else:
                ys=[sum(seg)/len(seg) if seg else 0 for seg in series]
                if self.error_bar.isChecked():
                    errs=[(sum((v-mean)**2 for v in seg)/len(seg))**.5 if seg else 0 for seg,mean in zip(series,ys)]
                    self.ax.errorbar(self.sweep_param_values[:len(ys)],ys,yerr=errs,label=name,color=color,linestyle='solid',capsize=2)
                else:self.ax.plot(self.sweep_param_values[:len(ys)],ys,label=name,color=color)
        if self.sweep_channel_info:self.ax.legend(loc='best')
        self.ax.set_xlabel(self.sweep_label); self.ax.set_ylabel('Voltage (V)'); self.canvas.draw()
        if self.raw_toggle.isChecked():
            self.ax_raw.clear(); self.ax_raw.grid(True)
            for i,series in enumerate(self.sweep_data):
                if series:
                    info=self.sweep_channel_info[i] if i<len(self.sweep_channel_info) else {'active':True,'name':f'CH{i}'}
                    if info['active']:
                        seg=series[-1]; self.ax_raw.plot([x/self.sweep_freq for x in range(len(seg))],seg,label=info['name'])
            if self.sweep_channel_info:self.ax_raw.legend(loc='best')
            self.ax_raw.set_xlabel('Time (s)'); self.ax_raw.set_ylabel('Voltage (V)'); self.canvas_raw.draw()

    def save_psa_data(self):
        # 掃引データと計測条件・チャンネル情報をCSV形式で保存する。
        if not self.sweep_param_values:return self._error('保存できるPSAデータがありません')
        path,_=QFileDialog.getSaveFileName(self,'PSAデータ保存','','CSV (*.csv)')
        if not path:return
        headers=['# [parameter]']
        columns=[]
        mode=self.sweep_operation; operation='MIN_MAX' if mode=='BDMODE' else 'MEAN_STDEV'
        for i,series in enumerate(self.sweep_data):
            info=self.sweep_channel_info[i] if i<len(self.sweep_channel_info) else {'active':True,'name':f'CH{i}'}
            if not info['active']:continue
            if mode=='BDMODE':
                headers.extend([f'[CH{i} min]',f'[CH{i} max]']); columns.extend([[min(x) if x else 0 for x in series],[max(x) if x else 0 for x in series]])
            else:
                headers.extend([f'[CH{i} mean]',f'[CH{i} stdev]']); columns.extend([[sum(x)/len(x) if x else 0 for x in series],[(sum((v-sum(x)/len(x))**2 for v in x)/len(x))**0.5 if x else 0 for x in series]])
        try:
            with open(path,'w',newline='',encoding='utf-8') as f:
                writer=csv.writer(f); f.write('# psa data (PyQt6 NEV client)\n'); f.write('# '+datetime.now().strftime('%d/%m/%Y %H:%M:%S')+'\n#\n')
                if self.active_set is not None:
                    pcol=self.pset_cols[self.active_set]; f.write('# parameter set: '+self.raw[0][pcol]+'\n')
                    f.write('# source CSV: '+str(self.csv_path or '')+'\n')
                    for i,row in enumerate(self.raw[1:]):
                        label=row[self.cols['LABEL']]
                        if label:f.write(f'# {label} : {row[pcol]}\n')
                f.write('# Data block: '+mode+'\n')
                f.write(f'# Number of channels: {sum(1 for x in self.sweep_channel_info if x["active"])}, Sampling rate: {self.sweep_freq} (Sps), Data mode: {operation}\n')
                f.write(f'# Initial delay: {self.idelay.value()} (s), Period per step: {self.period.value()} (s), Delay between steps: {self.delay.value()} (s)\n')
                f.write('# Sweep parameter: '+self.sweep_label+'\n')
                for i,info in enumerate(self.sweep_channel_info):
                    if info['active']:
                        f.write(f'# CH{i}: {info["name"]}\n')
                        f.write(f'#   Device: {info["device"].name}, Model: {info["device"].model}, Channel: {info["channel"]}, Coupling: {info["coupling"]}, Input range: {info["range"]} (V)\n')
                writer.writerow(headers)
                for r,param in enumerate(self.sweep_param_values):writer.writerow([param]+[col[r] if r<len(col) else '' for col in columns])
            self._log('PSA data saved: '+path)
        except Exception as e:self._error(str(e))

    def closeEvent(self,event):
        if self.sweep_active:
            try:self.scope.stop_psa()
            except Exception:pass
        self.timer.stop()
        if self.tcp and self.tcp.sock:
            try:self.tcp.sock.close()
            except Exception:pass
        event.accept()

def main():
    app=QApplication(sys.argv); win=NevClientWindow(sys.argv[1] if len(sys.argv)>1 else 'localhost'); win.show(); sys.exit(app.exec())
if __name__=='__main__':main()
