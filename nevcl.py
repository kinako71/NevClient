#!/usr/bin/env python3
"""PyQt6版NEVクライアントの起動処理と画面間の連携をまとめたファイル。

画面部品は nevcl_param.py / nevcl_psa.py / nevcl_vi.py、CSVの読み書きは nevcl_csvif.py、
機器通信は nevcl_tcp.py / nevcl_daqmx.py / nevcl_niscope.py に分けている。
画面配置や操作の流れを変えるときは主にこのファイルを編集する。
"""
import csv, sys
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import Qt, QTimer, QObject, QThread, pyqtSignal, QSettings
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QGroupBox, QFormLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QLabel, QTabWidget, QDoubleSpinBox, QComboBox,
    QPlainTextEdit, QHeaderView, QCheckBox, QSpinBox, QLineEdit)
import nevcl_tcp, nevcl_daqmx, nevcl_niscope
from nevcl_csvif import CsvParameterFile
from nevcl_param import ATTRS, ParameterPanel
from nevcl_psa import SweepPanel, PulseStimulusPanel, PRCPanel
from nevcl_vi import VoltageCurrentPanel, VoltageCurrentJob

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
        # チャンネルごとのプロットオフセットを次回起動時にも読み込めるよう保存する。
        self.plot_offset_settings=QSettings('KohnoLab','NEVClientPyQt')
        self.address=address; self.tcp=None; self.daq=None; self.scope=None; self.csv_model=None; self.csv_path=None; self.raw=[]; self.cols={}; self.pset_cols=[]; self.active_set=None
        self.voltage_current_results=None
        self.sweep_active=False; self.sweep_bookmark=0; self.sweep_dirs=[]; self.psa_data=[]; self.channel_rows=[]; self.scope_channels=[]; self.daqmx_ai_channels=[]; self.sweep_channel_info=[]; self.sweep_data=[]; self.sweep_param_values=[]; self.active_stim_target=None
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
        self.sweep_panel=SweepPanel(); self.fig=self.sweep_panel.fig; self.ax=self.sweep_panel.ax; self.canvas=self.sweep_panel.canvas; self.sweep_mode=self.sweep_panel.mode; self.sweep_param=self.sweep_panel.parameter; self.sweep_start=self.sweep_panel.start; self.sweep_stop=self.sweep_panel.stop; self.sweep_steps=self.sweep_panel.steps; self.sweep_dir=self.sweep_panel.direction; self.idelay=self.sweep_panel.initial_delay; self.period=self.sweep_panel.period; self.delay=self.sweep_panel.delay; self.frequency=self.sweep_panel.frequency; self.run_btn=self.sweep_panel.run_button; self.stop_btn=self.sweep_panel.stop_button; self.union_combo=self.sweep_panel.union; self.channel_plot_table=self.sweep_panel.channel_plot_table; self.sweep_x_axis=self.sweep_panel.x_axis
        self.error_bar=self.sweep_panel.error_bar; self.run_btn.clicked.connect(self.start_sweep); self.stop_btn.clicked.connect(self.stop_sweep); self.sweep_x_axis.currentIndexChanged.connect(self._plot_channel_settings_changed); self.sweep_panel.measurement_source.currentIndexChanged.connect(self._select_measurement_source); self.sweep_panel.save_button.clicked.connect(self.save_psa_data); self.sweep_panel.save_image_button.clicked.connect(self.save_psa_plot_image); self.sweep_mode.currentTextChanged.connect(self._select_sweep_mode); self.tabs.addTab(self.sweep_panel,'Parameter Sweeper')
        self.pulse_panel=PulseStimulusPanel(); self.pulse_panel.fire_button.clicked.connect(self.fire_pulse_stimulus); self.pulse_panel.save_button.clicked.connect(self.save_psa_data); self.pulse_panel.save_image_button.clicked.connect(self.save_psa_plot_image); self.tabs.addTab(self.pulse_panel,'Pulse Stimulus')
        self.prc_panel=PRCPanel(); self.tabs.addTab(self.prc_panel,'PRC')
        self.voltage_current_panel=VoltageCurrentPanel(); self.tabs.addTab(self.voltage_current_panel,'電圧-電流')
        self._setup_voltage_current_panel()
        self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.tabs.addTab(self.log,'通信ログ')
    def _setup_voltage_current_panel(self):
        panel=self.voltage_current_panel
        panel.run_button.clicked.connect(self.start_voltage_current_measurement)
        panel.stop_button.clicked.connect(self.stop_voltage_current_measurement)
        panel.save_csv_button.clicked.connect(self.save_voltage_current_csv)
        panel.save_image_button.clicked.connect(self.save_voltage_current_image)
        panel.show_error_bars.toggled.connect(self.redraw_voltage_current_plot)
    def start_voltage_current_measurement(self):
        # viplot.pyと同じ範囲・出力チャンネルの測定をワーカースレッドで開始する。
        panel=self.voltage_current_panel
        try:
            sweep_output=panel.sweep_channel.currentData()
            if sweep_output is None:raise ValueError('DAQmxの動的AOチャンネルを選択してください。先にサーバーへ接続してください。')
            segments=[(start.value(),stop.value(),points.value()) for start,stop,points in panel.segments]
            if any(start==stop for start,stop,_ in segments):raise ValueError('各掃引区間の開始値と終了値は異なる値にしてください')
            if not panel.visa_address.text().strip():raise ValueError('Keithley VISAアドレスを入力してください')
            settings={'visa_address':panel.visa_address.text().strip(),
                'sweep_channel':sweep_output,'segments':segments,'repeats':panel.repeats.value()}
            self.voltage_current_thread=QThread(self); self.voltage_current_job=VoltageCurrentJob(settings,self.daq); self.voltage_current_job.moveToThread(self.voltage_current_thread)
            self.voltage_current_thread.started.connect(self.voltage_current_job.run); self.voltage_current_job.progress.connect(panel.status.setText)
            self.voltage_current_job.point_updated.connect(self.update_voltage_current_plot)
            self.voltage_current_job.done.connect(self._voltage_current_finished); self.voltage_current_job.done.connect(self.voltage_current_thread.quit)
            self.voltage_current_thread.finished.connect(self.voltage_current_job.deleteLater); self.voltage_current_thread.finished.connect(self.voltage_current_thread.deleteLater)
            self.voltage_current_results=None; panel.show_results([],[],[]); panel.save_csv_button.setEnabled(False); panel.save_image_button.setEnabled(False)
            panel.run_button.setEnabled(False); panel.stop_button.setEnabled(True); panel.status.setText('接続中…'); self.voltage_current_thread.start()
        except Exception as error:self._error(str(error))
    def update_voltage_current_plot(self,point):
        # 各測定点の統計値を受け取り、測定中のグラフを更新する。
        count=point['count']
        if self.voltage_current_results is None:
            self.voltage_current_results={'voltage':[None]*count,'mean':[None]*count,'variance':[None]*count,'std':[None]*count}
        index=point['index']; result=self.voltage_current_results
        for key in ('voltage','mean','variance','std'):result[key][index]=point[key]
        panel=self.voltage_current_panel; panel.show_results(result['voltage'],result['mean'],result['std'])
        panel.save_csv_button.setEnabled(True); panel.save_image_button.setEnabled(True)
    def redraw_voltage_current_plot(self,*_args):
        # エラーバー設定を切り替えたら、保持中の測定値で描画し直す。
        result=self.voltage_current_results
        if result:
            self.voltage_current_panel.show_results(result['voltage'],result['mean'],result['std'])
    def stop_voltage_current_measurement(self):
        if hasattr(self,'voltage_current_job') and self.voltage_current_thread.isRunning():
            self.voltage_current_job.request_stop(); self.voltage_current_panel.status.setText('停止処理中…')
    def _voltage_current_finished(self,result,error):
        # 完了時に最終結果を保持し、保存操作を有効にする。
        panel=self.voltage_current_panel; panel.run_button.setEnabled(True); panel.stop_button.setEnabled(False)
        if error:
            if isinstance(error,InterruptedError):panel.status.setText('測定を停止しました')
            else:panel.status.setText('測定に失敗しました'); self._error(str(error))
            return
        self.voltage_current_results=result
        panel.show_results(result['voltage'],result['mean'],result['std'])
        panel.save_csv_button.setEnabled(True); panel.save_image_button.setEnabled(True)
        panel.status.setText(f'完了: {len(result["voltage"])}点 × {result["repeats"]}回')
    def save_voltage_current_csv(self):
        # 保存ボタンを押したときに保存先を選び、測定済み点の統計をCSVに書く。
        result=self.voltage_current_results
        if not result:return self._error('保存できる電圧–電流データがありません')
        default_path=Path(__file__).resolve().parent/'output.csv'
        path,_=QFileDialog.getSaveFileName(self,'電圧–電流データを保存',str(default_path),'CSV (*.csv)')
        if not path:return
        csv_path=Path(path)
        if csv_path.suffix.lower()!='.csv':csv_path=csv_path.with_suffix('.csv')
        try:
            with csv_path.open('w',newline='',encoding='utf-8') as output:
                writer=csv.writer(output); writer.writerow(['vdiff','mean','var','std'])
                writer.writerows((x,mean,variance,std) for x,mean,variance,std in zip(result['voltage'],result['mean'],result['variance'],result['std']) if mean is not None)
            self.voltage_current_panel.status.setText(f'CSVを保存しました: {csv_path.name}')
        except Exception as error:self._error(str(error))
    def save_voltage_current_image(self):
        # 保存ボタンから保存先を選び、現在のライブプロットを画像にする。
        if not self.voltage_current_results:return self._error('保存できるプロットがありません')
        default_path=Path(__file__).resolve().parent/'v-i.png'
        path,_=QFileDialog.getSaveFileName(self,'電圧–電流プロットを保存',str(default_path),'PNG (*.png)')
        if not path:return
        image_path=Path(path)
        if image_path.suffix.lower()!='.png':image_path=image_path.with_suffix('.png')
        try:
            self.voltage_current_panel.fig.savefig(image_path,dpi=200,bbox_inches='tight')
            self.voltage_current_panel.status.setText(f'画像を保存しました: {image_path.name}')
        except Exception as error:self._error(str(error))

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
            return 'DAQmx '+str(sum(map(len,[self.daq.aos,self.daq.ais,self.daq.dos,self.daq.dis])))+'台 (AO/AI/DO/DI) / NI-Scope '+str(self.scope.N_devices)+'台 / PSA unions '+str(self.scope.N_unions)+'個'
        def done(res,err):
            self.run_btn.setEnabled(True)
            if err:self.statusBar().showMessage('接続に失敗しました'); self._error(str(err)); return
            self.statusBar().showMessage(self.address+' に接続しました'); self._log(res); self._refresh_devices(); self.pulse_panel.set_outputs(self.daq.aos); self.voltage_current_panel.set_outputs(self.daq.aos); self._refresh_scope_channels()
        self._start_job(connect,done)

    def _refresh_devices(self):
        # PRCタブへの置き換え後は、一覧表を触らずPSA unionだけを更新する。
        if not self.daq:return
        self.union_combo.clear()
        if self.scope:
            for i in range(self.scope.N_unions):self.union_combo.addItem(f'Union {i}',i)

    def _refresh_scope_channels(self):
        # NI-ScopeとDAQmx AIの全チャンネルを保持し、選択された計測方式の一覧を表示する。
        self.scope_channels=[]
        if self.scope:
            for di,d in enumerate(self.scope.device_list):
                for ch in range(d.N_channels):self.scope_channels.append((di,d,ch))
        self.daqmx_ai_channels=[]
        if self.daq:
            for d in self.daq.ais:
                for ch in range(d.N_data):self.daqmx_ai_channels.append((d,ch))
        self._select_measurement_source()

    def _select_measurement_source(self,*_args):
        # 選択方式に応じて計測チャンネル一覧を切り替える。DAQmx側はDAQMXINFOのAI情報を使う。
        source=self.sweep_panel.measurement_source.currentData()
        is_daqmx=source=='daqmx'
        self.sweep_panel.union_label.setVisible(not is_daqmx); self.union_combo.setVisible(not is_daqmx)
        self.sweep_panel.channel_group.setTitle('DAQmx AIチャンネル（使用・Plot・Offset）' if is_daqmx else 'NI-Scopeチャンネル（使用・Plot・Offset）')
        if is_daqmx:
            channels=[('daqmx',d,ch) for d,ch in self.daqmx_ai_channels]
        else:
            channels=[('niscope',di,d,ch) for di,d,ch in self.scope_channels]
        plot_table=self.channel_plot_table; plot_table.setRowCount(len(channels))
        for r,item in enumerate(channels):
            active=QCheckBox(); active.setChecked(True); plot_table.setCellWidget(r,0,active)
            show=QCheckBox(); show.setChecked(True); show.toggled.connect(self._plot_channel_settings_changed); plot_table.setCellWidget(r,1,show)
            if is_daqmx:
                _,d,ch=item; channel_name=f'{d.name}/AI-{ch}'; offset_key=f'plot_offsets/DAQmx/{d.name}/AI-{ch}'
            else:
                _,di,d,ch=item; channel_name=f'{d.name}/CH{ch}'; offset_key=f'plot_offsets/{d.name}/CH{ch}'
            plot_table.setItem(r,2,QTableWidgetItem(channel_name))
            offset=QDoubleSpinBox(); offset.setRange(-10,10); offset.setSingleStep(.0001); offset.setDecimals(4)
            offset.setValue(self.plot_offset_settings.value(offset_key,0.0,type=float))
            offset.valueChanged.connect(lambda value,key=offset_key:self.plot_offset_settings.setValue(key,value)); offset.valueChanged.connect(self._plot_channel_settings_changed); plot_table.setCellWidget(r,3,offset)

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
        # セット一覧とNCMODE/BDMODEの掃引範囲を作り直す。
        self.set_table.setRowCount(len(self.pset_cols))
        for r,c in enumerate(self.pset_cols):self.set_table.setItem(r,0,QTableWidgetItem(self.raw[0][c])); self.set_table.setItem(r,1,QTableWidgetItem(''))
        names=self._labels(); self.sweep_param.clear(); self.sweep_param.addItems(names)
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
                io,ch=row[chcol].split('-',1); io=io.upper(); dev=self.daq.get_devinfo(row[devcol],io)
                if io not in ('AO','DO'):
                    raise ValueError('CSVの出力先は AO または DO を指定してください: '+row[devcol]+'/'+row[chcol])
                if dev is None:raise ValueError('DAQmx channel not found: '+row[devcol]+'/'+row[chcol])
                self.daq.set_ch(dev,int(ch),[float(row[pcol])])
            ret=self.daq.update()
            if ret is None:raise RuntimeError('DAQmx出力の更新に失敗しました')
            self._log('DAQmx outputs updated: '+str(ret)); self._refresh_devices()
        except Exception as e:self._error(str(e))
    def zero_clear(self):
        # DAQmx出力をゼロクリアする。
        if not self.daq:return self._error('先に接続してください')
        try:self.daq.zeroclear(); self._log('DAQmx zero clear completed')
        except Exception as e:self._error(str(e))

    def start_sweep(self):
        # 掃引・NI-Scope設定をサーバーへ送り、初期待機後にPSAを開始する。
        if self.sweep_panel.measurement_source.currentData()=='daqmx':
            return self._error('DAQmx AIチャンネル一覧は表示できますが、AI波形の取得処理は未実装です。現在のスイープ実行にはNI-Scopeを選択してください。')
        if not self.scope or not self.daq or not self.raw:return self._error('接続し、パラメータCSVを読み込んでください')
        try:
            if self.active_set is not None:
                pcol=self.pset_cols[self.active_set]
                for r,row in enumerate(self.raw[1:]):
                    value_widget=self._parameter_widget(r) if r<len(self.raw)-1 else None
                    if value_widget:
                        while len(row)<=pcol:row.append('')
                        row[pcol]=f'{value_widget.value():.4f}'
            union=self.union_combo.currentData(); i=self.sweep_attribute_index if self.sweep_attribute_index is not None else self.sweep_param.currentIndex(); label=self._labels()[i]; row=self.raw[i+1]; io,chstr=row[self.cols['CH']].split('-',1); io=io.upper(); dev=self.daq.get_devinfo(row[self.cols['DEV']],io); ch=int(chstr)
            if io != 'AO' or dev is None or dev.static or not dev.analog: raise ValueError('スイープ対象には接続済みの動的 AO 出力が必要です')
            dirs=[[1],[-1],[1,-1],[-1,1]][self.sweep_dir.currentIndex()]; self.sweep_dirs=dirs[1:]; direction=dirs[0]
            scope_devs=[]
            for r,(di,d,c) in enumerate(self.scope_channels):
                if self.channel_plot_table.cellWidget(r,0).isChecked() and di not in scope_devs:scope_devs.append(di)
            if not scope_devs:raise ValueError('NI-Scope channelを1つ以上有効にしてください')
            if self.scope.set_union_devlist(union,scope_devs) is None:raise RuntimeError('NI-Scope unionの機器設定に失敗しました')
            nchan=0
            channel_info=[]
            for di in scope_devs:
                devscope=self.scope.device_list[di]; conf=[]
                for r,(didx,d,c) in enumerate(self.scope_channels):
                    if didx!=di:continue
                    active=self.channel_plot_table.cellWidget(r,0).isChecked()
                    if active:conf.append([2,'DC'])
                    else:conf.append(['*','*'])
                    channel_info.append({'name':f'{d.name}/CH{c}','active':active,'show':self.channel_plot_table.cellWidget(r,1).isChecked(),'voffset':self.channel_plot_table.cellWidget(r,3).value(),'table_row':r,'device':d,'channel':c,'range':'2','coupling':'DC'})
                if self.scope.set_union_channelConf(union,di,0,conf) is None:raise RuntimeError(f'NI-Scope機器{di}のチャンネル設定に失敗しました')
                nchan+=devscope.N_channels
            freq=self.frequency.currentData(); dlen=int((self.period.value()+self.delay.value())*freq)
            if self.scope.set_union_dlen(union,dlen) is None:raise RuntimeError('NI-Scopeデータ長の設定に失敗しました')
            if self.scope.set_union_freq(union,freq) is None:raise RuntimeError('NI-Scopeサンプリング周波数の設定に失敗しました')
            stop=self.sweep_stop.value() if direction>0 else self.sweep_start.value(); start=self.sweep_start.value() if direction>0 else self.sweep_stop.value()
            if self.scope.set_psa(union,[dev.taskType,dev.taskNo,ch],[start,stop,self.sweep_steps.value()],int(self.delay.value()*freq)) is None:raise RuntimeError('SET PSAがサーバーに拒否されました。ターミナルのサーバーエラーを確認してください')
            self.daq.set_ch(dev,ch,[start])
            # 固定値は画面の一覧に依存せず、CSVの選択中モード列から直接DAQmxへ送る。
            selected_mode=self.sweep_mode.currentText(); mode_col=self.cols.get(selected_mode)
            if mode_col is not None:
                for orow in self.raw[1:]:
                    val=orow[mode_col] if mode_col<len(orow) else ''
                    if not val or val.startswith('#'):continue
                    oio,ochstr=orow[self.cols['CH']].split('-',1); oio=oio.upper(); od=self.daq.get_devinfo(orow[self.cols['DEV']],oio); och=int(ochstr)
                    if oio not in ('AO','DO') or od is None: raise ValueError('固定出力先が見つからないか、出力種別が不正です: '+orow[self.cols['DEV']]+'/'+orow[self.cols['CH']])
                    self.daq.set_ch(od,och,[float(val)])
            if self.daq.update() is None:raise RuntimeError('掃引開始前のDAQmx出力更新に失敗しました')
            self.sweep_union=union; self.sweep_label=label; self.sweep_operation=self.sweep_mode.currentText(); self.sweep_bookmark=0; self.sweep_nchan=nchan; self.sweep_freq=freq; self.sweep_channel_info=channel_info; self.sweep_data=[[] for _ in range(nchan)]; self.sweep_param_values=[]; self._refresh_sweep_x_axis(channel_info)
            self.sweep_active=True; self.run_btn.setEnabled(False); self.stop_btn.setEnabled(True)
            QTimer.singleShot(int(self.idelay.value()*1000),self._launch_sweep)
        except Exception as e:self._error(str(e))
    def _launch_sweep(self):
        if not self.sweep_active:return
        try:
            if self.scope.run_psa() is None:raise RuntimeError('RUN PSAがサーバーに拒否されました。ターミナルのサーバーエラーを確認してください')
            self.timer.start(); self._log('PSA started: '+self.sweep_label)
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
                result=self.scope.get_psa_data(self.sweep_bookmark,None)
                if result is None:raise RuntimeError('GET PSA DATAに失敗しました。ターミナルのサーバーエラーを確認してください')
                last,data=result
                if data:
                    for param,channels in data:
                        self.sweep_param_values.append(param)
                        for i,segment in enumerate(channels):
                            if i<len(self.sweep_data):self.sweep_data[i].append(segment)
                self.sweep_bookmark=last+1; self.statusBar().showMessage(f'PSA {state}: {self.sweep_bookmark} points, current {pvalue:g} V'); self._update_plots()
            if state!='RUNNING':
                if self.sweep_dirs:
                    direction=self.sweep_dirs.pop(0); self.sweep_bookmark=0; start=self.sweep_start.value() if direction>0 else self.sweep_stop.value(); end=self.sweep_stop.value() if direction>0 else self.sweep_start.value(); idx=self._labels().index(self.sweep_label); row=self.raw[idx+1]; dev=self.daq.get_devinfo(row[self.cols['DEV']],row[self.cols['CH']].split('-',1)[0]); ch=int(row[self.cols['CH']].split('-',1)[1])
                    if self.scope.set_psa(self.sweep_union,[dev.taskType,dev.taskNo,ch],[start,end,self.sweep_steps.value()],int(self.delay.value()*self.sweep_freq)) is None:raise RuntimeError('逆方向のSET PSAがサーバーに拒否されました')
                    if self.scope.run_psa() is None:raise RuntimeError('逆方向のRUN PSAがサーバーに拒否されました')
                else:self.stop_sweep()
        except Exception as e:self.stop_sweep(); self._error(str(e))
    def _plot_channel_settings_changed(self,*_args):
        # 表での表示切替・オフセット変更を既存データへ即時反映する。
        for info in self.sweep_channel_info:
            r=info.get('table_row')
            if r is None or r>=self.channel_plot_table.rowCount():continue
            info['show']=self.channel_plot_table.cellWidget(r,1).isChecked()
            info['voffset']=self.channel_plot_table.cellWidget(r,3).value()
        if self.sweep_data and hasattr(self,'sweep_label'):
            self._update_plots()

    def _refresh_sweep_x_axis(self,channel_info):
        """掃引パラメータと有効な取得チャンネルを横軸候補にする。"""
        previous=self.sweep_x_axis.currentData(); self.sweep_x_axis.blockSignals(True); self.sweep_x_axis.clear()
        self.sweep_x_axis.addItem('掃引パラメータ',-1)
        for idx,info in enumerate(channel_info):
            if info['active']:self.sweep_x_axis.addItem(info['name'],idx)
        selected=self.sweep_x_axis.findData(previous)
        self.sweep_x_axis.setCurrentIndex(selected if selected>=0 else 0); self.sweep_x_axis.blockSignals(False)

    def _update_plots(self):
        # 横軸に掃引パラメータまたは取得チャンネルの平均値を選び、両プロットを同期する。
        x_index=self.sweep_x_axis.currentData()
        if x_index is None or x_index<0 or x_index>=len(self.sweep_data):x_index=-1
        x_label=self.sweep_label if x_index<0 else self.sweep_channel_info[x_index]['name']
        colors=['#ff0000','#0000ff','#00aa00','#ff00ff','#cc9900','#00aaaa','#883355','#335588']
        for ax in (self.ax,self.pulse_panel.ax):
            ax.clear(); ax.set_box_aspect(1); ax.grid(True)
            x_source=self.sweep_data[x_index] if x_index>=0 else None
            for i,series in enumerate(self.sweep_data):
                info=self.sweep_channel_info[i] if i<len(self.sweep_channel_info) else {'active':True,'show':True,'name':f'CH{i}','voffset':0.0}
                if not info.get('show',info['active']):continue
                color=colors[i%len(colors)]; name=info['name']; offset=info.get('voffset',0.0)
                n=len(series)
                if x_source is not None:n=min(n,len(x_source))
                x=self.sweep_param_values[:n] if x_source is None else [sum(seg)/len(seg) if seg else 0 for seg in x_source[:n]]
                if self.sweep_operation=='BDMODE':
                    low=[(min(seg) if seg else 0)+offset for seg in series[:n]]; high=[(max(seg) if seg else 0)+offset for seg in series[:n]]
                    ax.plot(x,low,label=f'{name} min',color=color); ax.plot(x,high,label=f'{name} max',color=color)
                else:
                    means=[sum(seg)/len(seg) if seg else 0 for seg in series[:n]]; ys=[mean+offset for mean in means]
                    if self.error_bar.isChecked():
                        yerr=[(sum((v-mean)**2 for v in seg)/len(seg))**.5 if seg else 0 for seg,mean in zip(series[:n],means)]
                        xerr=None if x_source is None else [(sum((v-mean)**2 for v in seg)/len(seg))**.5 if seg else 0 for seg in x_source[:n]]
                        ax.errorbar(x,ys,xerr=xerr,yerr=yerr,label=name,color=color,linestyle='solid',capsize=2)
                    else:ax.plot(x,ys,label=name,color=color)
            if self.sweep_channel_info:ax.legend(loc='best')
            ax.set_xlabel(x_label); ax.set_ylabel('Voltage (V)')
        self.canvas.draw(); self.pulse_panel.canvas.draw_idle()

    def fire_pulse_stimulus(self):
        """Pulse Stimulusタブで設定した波形を選択中のDAQmx出力へ送る。"""
        if not self.daq:return self._error('先にDAQmx機器へ接続してください')
        selected=self.pulse_panel.output.currentData()
        if not selected:return self._error('出力チャンネルがありません')
        dev,ch=selected
        try:
            if self.active_stim_target and self.active_stim_target!=(dev,ch):
                old_dev,old_ch=self.active_stim_target; old_dev.set_stim(old_ch,[0]); self.daq.set_dao_data(old_dev,0,-1,True); self.daq.run_dao(old_dev)
            dt_ms=self.pulse_panel.dt.value(); duration_ms=self.pulse_panel.duration.value(); n=max(2,int(duration_ms/dt_ms)+1)
            dev.L_data=n; dev.freq=1000.0/dt_ms
            if self.daq.set_dao_cfg(dev) is None:raise RuntimeError('DAQmx波形設定に失敗しました')
            for channel in range(dev.N_data):dev.set_stim(channel,[0.0]*n)
            dev.set_stim(ch,[value/1000.0 for value in self.pulse_panel.waveform])
            if self.daq.set_dao_data(dev,0,-1,True) is None:raise RuntimeError('DAQmx波形データ送信に失敗しました')
            if self.daq.run_dao(dev) is None:raise RuntimeError('DAQmx波形出力に失敗しました')
            self.active_stim_target=(dev,ch); self._log(f'Pulse stimulus output: {dev.name}/AO-{ch}, {n} samples at {dev.freq:g} S/s')
        except Exception as e:self._error(str(e))

    def save_psa_data(self):
        # 掃引データと計測条件・チャンネル情報をCSV形式で保存する。
        if not self.sweep_param_values:return self._error('保存できるPSAデータがありません')
        path,_=QFileDialog.getSaveFileName(self,'PSAデータ保存','output.csv','CSV (*.csv)')
        if not path:return
        if Path(path).suffix.lower()!='.csv':path += '.csv'
        headers=['# [parameter]']
        columns=[]
        mode=self.sweep_operation; operation='MIN_MAX' if mode=='BDMODE' else 'MEAN_STDEV'
        for i,series in enumerate(self.sweep_data):
            info=self.sweep_channel_info[i] if i<len(self.sweep_channel_info) else {'active':True,'name':f'CH{i}'}
            if not info.get('show',info['active']):continue
            offset=info.get('voffset',0.0)
            if mode=='BDMODE':
                headers.extend([f'[CH{i} min]',f'[CH{i} max]']); columns.extend([[(min(x) if x else 0)+offset for x in series],[(max(x) if x else 0)+offset for x in series]])
            else:
                # 平均を各サンプルごとに再計算せず、区間を一度走査して統計量を作る。
                means=[]; stdevs=[]
                for samples in series:
                    if not samples:means.append(0); stdevs.append(0); continue
                    mean=sum(samples)/len(samples)
                    means.append(mean+offset)
                    stdevs.append((sum((value-mean)**2 for value in samples)/len(samples))**0.5)
                headers.extend([f'[CH{i} mean]',f'[CH{i} stdev]']); columns.extend([means,stdevs])
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
                f.write(f'# Number of channels: {sum(1 for x in self.sweep_channel_info if x.get("show",x["active"]))}, Sampling rate: {self.sweep_freq} (Sps), Data mode: {operation}\n')
                f.write(f'# Initial delay: {self.idelay.value()} (s), Period per step: {self.period.value()} (s), Delay between steps: {self.delay.value()} (s)\n')
                f.write('# Sweep parameter: '+self.sweep_label+'\n')
                for i,info in enumerate(self.sweep_channel_info):
                    if info.get('show',info['active']):
                        f.write(f'# CH{i}: {info["name"]}\n')
                        f.write(f'#   Device: {info["device"].name}, Model: {info["device"].model}, Channel: {info["channel"]}, Coupling: {info["coupling"]}, Input range: {info["range"]} (V), Plot offset: {info.get("voffset",0.0)} (V)\n')
                writer.writerow(headers)
                for r,param in enumerate(self.sweep_param_values):writer.writerow([param]+[col[r] if r<len(col) else '' for col in columns])
            self._log('PSA data saved: '+path)
        except Exception as e:self._error(str(e))

    def save_psa_plot_image(self):
        # PSAプロット画像の保存先をclientフォルダにした状態で保存ダイアログを開く。
        client_dir=Path(__file__).resolve().parent
        default_path=client_dir/'output.png'
        path,_=QFileDialog.getSaveFileName(self,'プロット画像保存',str(default_path),'PNG画像 (*.png)')
        if not path:return
        if Path(path).suffix.lower()!='.png':path += '.png'
        try:
            self.fig.savefig(path,dpi=200,bbox_inches='tight')
            self._log('PSA plot image saved: '+path)
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
