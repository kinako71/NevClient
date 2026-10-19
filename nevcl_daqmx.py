#!/usr/bin/python3
"""NEVサーバーのDAQmx入出力を操作する通信・データ層。

画面からは nevcl_sysDAQmx.set_ch() で値を登録し、update() でサーバーへ反映する。
機器メタデータとチャンネルデータの形式は nevcl_devDAQmx を参照。
"""
#
# nev client daqmx subsystem
# Takashi Kohno
# Rev. 1.0,  12/10/2017
#
#
# HISTORY
# Rev. 1.0,  12/10/2017
# - new function : nevcl_sysDAQmx.zeroclear()
# Rev. 0.2,  15/08/2017
#
# *NOTE*
import nevcl_sys

# Instance for reference by outside the module
# - needs initialization by upper classes
sys = None

# Class for accessing daqmx subsystem in the nev_server
class nevcl_sysDAQmx():
    """DAQmx機器一覧を読み込み、アナログ／デジタル出力を更新する。"""
    """
    Rev. 1.0 (15/08/2017)
    TODO:
    - error handling (disconnection/unexpected response)
    """
    ## [ tcp connection ]
    tcpcon = None

    ## [ device lists ]
    dev_dict = {}  # dictionary from device name to [ static/dynamic, device model ]

    ## [ variables ]
    auto_update = False  # auto update mode(self.update() is invoked when set functions are executed)
    update_list = {}     # set of task names to be updated

    def __init__(self, tcpCon):
        self.tcpcon = tcpCon
        self.init()

    def init(self):
        """GET DAQMXINFOを解析し、AO/AI/DO/DIの機器情報を登録する。"""
        self.tcpcon.send("GET DAQMXINFO")
        res = self.tcpcon.recv()
        if res is None:
            nevcl_sys.logger.log("disconnected from server", 2)
            return None
        if not res or res[0] != "#DAQMXINFO":
            nevcl_sys.logger.log("unexpected response from server", 2)
            return res

        self.aos = []
        self.ais = []
        self.dos = []
        self.dis = []
        self.dev_dict = {}

        tag_to_list = {
            "AO": self.aos,
            "AI": self.ais,
            "DO": self.dos,
            "DI": self.dis,
        }

        for inf in res[1:]:
            tag, dl = self._analyze_inf(inf)
            if tag not in tag_to_list:
                continue

            for idx, dev in enumerate(dl):
                # L_data / freqで静的・動的タスクを判定する。
                # 例: AO(...,1024,100000,...) は波形出力、DO(...,1,0,...) は静的出力。
                try:
                    l_data = int(dev[4])
                    freq = float(dev[5])
                except (ValueError, IndexError):
                    l_data, freq = 1, 0.0
                is_static = (l_data <= 1 or freq <= 0.0)
                is_analog = tag in ("AO", "AI")

                nd = nevcl_devDAQmx(
                    dev, [is_static, is_analog, tag, idx]
                )
                tag_to_list[tag].append(nd)
                self.dev_dict[nd.name] = [
                    "static" if is_static else "dynamic", nd.model
                ]


        self.update_list = set()
        return []

    def _analyze_inf(self, inf):
        """ [internal function] analyze devinfo given by nev_server (response to GET DAQMXINFO)  """
        ## [ find the tag ]
        idx = inf.find("[")
        if idx < 0:
            return inf, []
        tag = inf[0:idx]
        src = inf[idx:]
        ## [ scan device list ]
        dev_list = []
        blks = src.split("]")
        for blk in blks:
            dev_info = blk[1:].split(",")
            if dev_info[0] == "":  # ignore current dev_info if ID is empty
                continue
            l = len(dev_info)
            # fill empty fields
            for idx in range(l, 7):
                dev_info.append("")
            dev_list.append(dev_info)
        return tag, dev_list

    def get_devinfo(self, dev_name, ioType):
        """機器名とAO/DO種別から、出力機器オブジェクトを返す。"""
        ioType = ioType.upper()
        if ioType == "AO":
            dev_list = self.aos
        elif ioType == "DO":
            dev_list = self.dos
        elif ioType == "AI":
            dev_list = self.ais
        elif ioType == "DI":
            dev_list = self.dis
        else:
            return None

        for dev in dev_list:
            if dev.name == dev_name:
                return dev
        return None

    def set_ch(self, dev_info, ch, val):
        # 出力値を機器オブジェクトに記録する。送信はupdate()で行う。
        res = dev_info.set_ch(ch, val)
        if res > 0:
            self.update_list.add(dev_info)
        if self.auto_update:
            self.update()

    def update(self, *args):
        # 変更された出力をサーバーへ送信し、動的出力ならタスクを実行する。
        """ send SET command to the nev_server and update the output voltages
            - args[0] : list of dev_infos to be updated
            - dev_infos in self.update_list are updated if args[0] is empty
            - returns "OK" if success  ## This should be refined
        """
        ## [ update devices in self.update_list if no argument is given ]
        print("# nevcl_sysDAQmx.update()")
        if len(args) > 0:
            dev_infos = list(set(args[0]))
        else:
            dev_infos = list(self.update_list)
        print("  updating", len(dev_infos), "devices")
        ## [ send commands to nev_server ]
        for dev_info in dev_infos:
            self.print_devinfo(dev_info)
            comstr = "SET " + dev_info.taskType + " " + str(dev_info.taskNo)
            if dev_info.static:  # static devices
                for val in dev_info.data:
                    comstr += " " + str(val[0])
                print("  sending:", comstr)
                self.tcpcon.send(comstr)
                res = self.tcpcon.recv()
                print("  response:", res)
                if res is None:  # disconnected
                    return None
                if res[0] != "": # unexpected response
                    print("! unexpected response")
                    return None
                self.update_list.remove(dev_info)
                continue
            else:  # dynamic devices
                # update the data for all the channels
                if self.set_ao_data(dev_info, 0, -1, False) == None:
                    continue
                # issue RUN AO command
                if self.run_ao(dev_info) == None:
                    continue
                # remove dev_info from self.update_list
                self.update_list.remove(dev_info)
                continue
        print("# nevcl_sysDAQmx.update() complete")
        return "OK"

    def set_ao_data(self,
                     dev_info: "target device's nevcl_devDAQmx",
                     ch_start: "index of the first channel",
                     ch_end  : "index of the last channel (-1 for the device's last channel)",
                     stim    : "set True to activate nevcl_devDAQmx.stim"):
        """
        send SET AO command to a dynamic analog task
        - Data is set only for a range of channels whose index is from ch_start to ch_end.
          To specify all the channels in the device, set ch_start = 0, ch_end = -1.
        """
        print("# set_ao_data()")
        ## [ send SET AO command ]
        comstr = "SET " + dev_info.taskType + " " + str(dev_info.taskNo) + " " + str(ch_start)
        print("  sending:", comstr)
        self.tcpcon.send(comstr)
        res = self.tcpcon.recv()
        print("  response:", res)
        if res is None:  # disconnected
            return None
        if res[0] != "#SET" + dev_info.taskType + " " + str(dev_info.taskNo): # unexpected response
            print("! unexpected response")
            return None
        ## [ calculate the last channel's index + 1 ]
        if ch_end < 0:
            ch_last = dev_info.N_data
        else:
            ch_last = ch_end + 1
        ## [ generate data string ]
        datstr = ""
        for idx in range(ch_start, ch_last):
            val = dev_info.data[idx][:]
            len_val = len(val)
            if stim:  # add dev_info.stim
                val_last = val[-1]
                stm = dev_info.stim[idx]
                len_stm = len(stm)
                print("  len_val  =", str(len_val))
                print("  len_stim =", str(len_stm))
                for i in range(len_val):
                    if i < len_stm:
                        val[i] += stm[i]
                for j in range(i + 1, len_stm):
                    val.append(val_last + stm[j])
            if len(val) > 1:
                datstr += str(val) + " "
            else:
                datstr += str(val[0]) + " "
        datstr += "#OK\n"
        ## [ send the data string ]
        print("  sending:", datstr)
        self.tcpcon.send(datstr)
        res = self.tcpcon.recv()
        print("  response:", res)
        if res is None:  # disconnected
            return None
        if res[0] != "":  # unexpected response
            print("! unexpected response:", res)
            return None
        dev_info.stim_active = stim
        return res
        
    def run_ao(self, dev_info):
        """ 
        send RUN AO command to the nev_server
        - returns response of the RUN AO command
        """
        self.tcpcon.send("RUN " + dev_info.taskType + " " + str(dev_info.taskNo))
        res = self.tcpcon.recv()
        if res is None:  # disconnected
            return None
        if res[0] != "":  # unexpected response
            print("! unexpected response:", res)
            return None
        return res

    def set_ao_cfg(self, *args):
        """
        send SET AO DLEN and SET AO FREQ commands to the nev_server
        - arguments: dev_infos
          if arguments are not given, all the devices are configured
          with the parameter for the FIRST DEVICE in the AO device list
        """
        if len(args) == 0:
            cmnd_parsets = [ [ '*', str(self.aos[0].L_data), str(self.aos[0].freq) ] ]
        else:
            cmnd_parsets = [ [ str(dev_info.taskNo), str(dev_info.L_data), str(dev_info.freq) ]
                          for dev_info in args ]

        for cmnd_parset in cmnd_parsets:
            cmnd_set = [ "SET AO DLEN " + cmnd_parset[0] + " " + cmnd_parset[1],
                         "SET AO FREQ " + cmnd_parset[0] + " " + cmnd_parset[2] ]
            for cmnd in cmnd_set:
                self.tcpcon.send(cmnd)
                res = self.tcpcon.recv()
                if res is None:
                    return None
                if res[0] != "":  # unexpected response
                    print("! sysDAQmx error:", cmnd, "returned", res)
                    return None
            return res

    def zeroclear(self):
        # 対応する全DAQmx出力をゼロにする。
        """ 
        send ZEROCLEAR command to the nev_server
        - returns response of the ZEROCLEAR command
        """
        self.tcpcon.send("ZEROCLEAR")
        res = self.tcpcon.recv()
        if res is None:  # disconnected
            return None
        if res[0] != "":  # unexpected response
            print("! unexpected response:", res)
            return None
        return res

    def print_devinfo(self, dev_name):
        if isinstance(dev_name, nevcl_devDAQmx):
            dev_info = dev_name
        else:
            if dev_name not in self.dev_dict:
                return None
            dev_info = self.get_devinfo(dev_name, "AO") or self.get_devinfo(dev_name, "DO")
            if dev_info is None:
                dev_info = self.get_devinfo(dev_name, "AI") or self.get_devinfo(dev_name, "DI")
            if dev_info is None:
                return None
        print("id    :", dev_info.id)
        print("name  :", dev_info.name)
        print("model :", dev_info.model)
        print("N_data:", dev_info.N_data)
        print("L_data:", dev_info.L_data)
        print("freq  :", dev_info.freq)
        print("state :", dev_info.state)
        print("static:", dev_info.static)
        print("analog:", dev_info.analog) 
        print("taskNo:", dev_info.taskNo)
        print("taskType:", dev_info.taskType)
        for idx, val in enumerate(dev_info.data):
            print(idx, ": ", val)




class nevcl_sysDAQmx_dummy():
    """実機通信を行わない開発・画面確認用のDAQmx代替実装。"""

    def __init__(self):
        self.tcpcon = "stand_alone"
        self.auto_update = False
        self.update_list = set()
        self.init()

    def init(self):
        self.aos = [
            nevcl_devDAQmx(["0", "PXI1Slot2", "PXIe-6739", 64, 1024, 100000.0, 1],
                           [False, True, "AO", 0]),
            nevcl_devDAQmx(["1", "PXI1Slot3", "PXIe-6739", 64, 1024, 100000.0, 1],
                           [False, True, "AO", 1]),
        ]
        self.ais = [
            nevcl_devDAQmx(["0", "PXI1Slot4", "PXIe-4481", 6, 1, 0.0, 0],
                           [True, True, "AI", 0]),
            nevcl_devDAQmx(["1", "PXI1Slot5", "PXIe-4481", 6, 1, 0.0, 0],
                           [True, True, "AI", 1]),
        ]
        self.dos = [
            nevcl_devDAQmx(["0", "PXI1Slot2", "PXIe-6739", 20, 1, 0.0, 0],
                           [True, False, "DO", 0]),
            nevcl_devDAQmx(["1", "PXI1Slot3", "PXIe-6739", 20, 1, 0.0, 0],
                           [True, False, "DO", 1]),
        ]
        self.dis = [
            nevcl_devDAQmx(["0", "PXI1Slot2", "PXIe-6739", 20, 1, 0.0, 0],
                           [True, False, "DI", 0]),
            nevcl_devDAQmx(["1", "PXI1Slot3", "PXIe-6739", 20, 1, 0.0, 0],
                           [True, False, "DI", 1]),
        ]
        self.dev_dict = {}
        for group in (self.aos, self.ais, self.dos, self.dis):
            for dev in group:
                self.dev_dict[dev.name] = ["static" if dev.static else "dynamic", dev.model]
        self.update_list = set()
        return []

    def get_devinfo(self, dev_name, ioType):
        groups = {"AO": self.aos, "AI": self.ais, "DO": self.dos, "DI": self.dis}
        for dev in groups.get(ioType.upper(), []):
            if dev.name == dev_name:
                return dev
        return None

    def set_ch(self, dev_info, ch, val):
        res = dev_info.set_ch(ch, val)
        if res > 0:
            self.update_list.add(dev_info)
        if self.auto_update:
            self.update()
        return res

    def update(self, *args):
        dev_infos = list(set(args[0])) if args else list(self.update_list)
        for dev_info in dev_infos:
            if dev_info.taskType not in ("AO", "DO"):
                print("  skipping input device:", dev_info.name)
                continue
            command = "SET " + dev_info.taskType + " " + str(dev_info.taskNo)
            command += " " + " ".join(str(values[0]) for values in dev_info.data)
            print("  simulated sending:", command)
            if not dev_info.static:
                print("  simulated sending: RUN " + dev_info.taskType + " " + str(dev_info.taskNo))
            self.update_list.discard(dev_info)
        return "OK"

    def print_devinfo(self, dev_name):
        if isinstance(dev_name, nevcl_devDAQmx):
            dev_info = dev_name
        else:
            dev_info = next((d for group in (self.aos, self.ais, self.dos, self.dis)
                             for d in group if d.name == dev_name), None)
        if dev_info is None:
            return None
        dev_info.dump()



class nevcl_devDAQmx():
    """1台のDAQmxタスクと、そのチャンネルごとの出力値を保持する。"""
    """
    Rev. 1.0 (15/08/2017)
    dev_info
    """
    id = None
    name = None
    model = None
    N_data = 0
    L_data = 0
    freq = 0.0
    state = 0
    static = True    # True when the task is for static operation (freq = 0.0, L_data = 1)
    analog = True    # True when the task supports analog signal
    taskType = None  # AO/AI/DO/DI (string)
    taskNo = -1      # task No. used in the command set of nev_server
    data = []        # 2-d array data (an array for each channel)
    stim = []        # 2-d array data (an array for each channel)
    stim_active = False  # the last SET command was with stimulus (manipulated by nevcl_sysDAQmx)
    data_updated = False

    default_value = 0
    default_value_stim = 0

    def __init__(self, *args):
        """ arg[0]: a member of daqmx device info obtained by self._analyze_inf()
            arg[1]: [ static, analog ]
        """
        if len(args) > 0:
            self.set_devinfo(args[0])
        if len(args) > 1:
            self.set_devtype(args[1])
        self.stim_active = False
        self.data_updated = False

    def set_devinfo(self, devinfo):
        self.id = devinfo[0]
        self.name = devinfo[1]
        self.model = devinfo[2]
        self.N_data = int(devinfo[3])
        self.L_data = int(devinfo[4])
        self.freq = float(devinfo[5])
        self.state = int(devinfo[6])
        self.data = [ [ self.default_value ] for idx in range(self.N_data) ]
        self.stim = [ [ self.default_value ] for idx in range(self.N_data) ]

    def set_devtype(self, devtype):
        self.static = devtype[0]
        self.analog = devtype[1]
        self.taskType = devtype[2]
        self.taskNo = devtype[3]

    def set_ch(self, ch: "channel No.", data: "data"):
        if ch >= self.N_data:
            return 0
        if len(data) > self.L_data:
            self.data[ch] = data[:self.L_data]
        else:
            self.data[ch] = data[:]
        self.data_updated = True
        return 1
            
    def get_ch(self, ch: "channel No."):
        if ch >= self.N_data:
            return None
        return self.data[ch]
            
    def set_chs(self, chs: "array of channel No.", data: "array of data"):
        res = 0
        if len(chs) == 0:  # all channels are specified if chs is empty
            chs = [ idx for idx in range(self.N_data) ]
        elif max(chs) >= self.N_data:
            return 0
        for idx, ch in enumerate(chs):
            self.set_ch(ch, data[idx])
            res += 1
        self.data_updated = True
        return res

    def get_chs(self, chs: "array of channel No."):
        res = []
        if len(chs) == 0:  # all channels are specified if chs is empty
            chs = [ idx for idx in range(self.N_data) ]
        for idx, ch in enumerate(chs):
            d = self.get_ch(ch)
            if d is None:
                return None
            res.append(d)
        return res

    def set_stim(self, ch: "channel No.", stim: "stimulus data (list)"):
        if ch >= self.N_data:
            return 0
        self.stim[ch] = stim

    def get_stim(self, ch: "channel No."):
        if ch >= self.N_data:
            return []
        return self.stim[ch]

    def dump(self):
        print("ID / NAME / MODEL:", str(self.id), "/", self.name, "/", self.model)
        print("N_DATA / L_DATA / FREQ / STATE:",
              str(self.N_data), "/", str(self.L_data), "/", str(self.freq), "/", str(self.state))
        if self.static:
            sd = "static"
        else:
            sd = "dynamic"
        if self.analog:
            ad = "analog"
        else:
            ad = "digital"
        print(sd, ad, "device")
        print("taskType / taskNo:", self.taskType, "/", str(self.taskNo))
        print(self.data)
            
        
if __name__ == "__main__":
    import sys
    import nevcl_tcp

    nevcl_sys.nevcl_Logger()
    addr = str(sys.argv[1]) if len(sys.argv) > 1 else "localhost"
    tcpc = nevcl_tcp.nevcl_tcpCon(addr)
    daqmx_sys = nevcl_sysDAQmx(tcpc)

    for label, devices in (("AO", daqmx_sys.aos), ("AI", daqmx_sys.ais),
                           ("DO", daqmx_sys.dos), ("DI", daqmx_sys.dis)):
        print(label)
        for dev in devices:
            print(dev.id, dev.name, dev.model, dev.N_data,
                  dev.L_data, dev.freq, dev.state)
