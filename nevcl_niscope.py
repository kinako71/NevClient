#!/usr/bin/python3
"""NEVサーバーのNI-ScopeとPSA（Parameter Sweeper）を操作する通信層。

union設定を送った後、set_psa() / run_psa() で掃引し、get_psa_stat() と
get_psa_data() を繰り返して進捗と波形データを取得する。
"""
#
# nev client niscope subsystem
# Takashi Kohno
# Rev. 1.0,  12/10/2017
#
#
# HISTORY
# Rev. 1.0,  12/10/2017
# - inserted a logging code in nevcl_sysNIScope.get_psa_data()
# Rev. 0.1,  14/09/2017
#
import nevcl_sys

# Instance for reference by outside the module
# - needs initialization by upper classes
sys = None

# Class for accessing niscope subsystem in the nev_server
class nevcl_sysNIScope():
    """NI-Scope機器、union設定、PSA実行状態を扱うサーバーAPI。"""
    """
    Rev. 0.1 (11/09/2017)
    TODO:
    - error handling (disconnection/unexpected response)
    """
    ## [ tcp connection ]
    tcpcon = None

    ## [ device information ]
    device_list = []  # list of device infos (nevcl_devNIScope)
    N_devices = 0

    ## [ union information ]
    union_list = []  # list of union infos (nevcl_uniNIScope)
    N_unions = 0

    ## [ variables ]
    status = None

    def __init__(self, tcpCon):
        self.tcpcon = tcpCon
        self.init()

    def init(self):
        # 機器数とunion数を問い合わせ、サーバー上の既存設定も読み込む。
        """
        (re-)initialize NISCOPE subsystem
        - get list of installed devices in nev_server by GET NISCOPEINFO
        - register the devices to self.device_list

        Return value:
        - None : tcp connection has been killed unexpectedly / unexpected response from server
        - number of devices found : success
        """
        self.status = "INIT"
        ## [ initialize device list (self.device_list and self.N_devices) ]
        # send command and receive data
        comstr = "GET NISCOPEINFO"
        resstr = "#NISCOPEINFO"
        res = self._get_server_response(comstr, resstr)
        if res is None:
            return None
        # initialize self.device_list and self.N_devices (uses self._analyze_inf()) ]
        self.device_list = []
        self.device_dict = {}   # not userd for now
        for info in res:
            dl = self._analyze_inf(info)
            self.device_list.extend(dl)
        self.N_devices = len(self.device_list)
        ## [ initialize union list ]
        # send command receive data
        comstr = "GET NSU NUM"
        resstr = "#NSUNUM"
        res = self._get_server_response(comstr, resstr)
        if res is None:
            return None
        # set self.union_list and self.N_unions
        self.N_unions = int(res[0])
        self.union_list = [ nevcl_uniNIScope() for i in range(0, self.N_unions) ]
        for idx in range(0, self.N_unions):
            self.load_union(idx)
        self.status = "READY"
        return self.N_devices

    def _analyze_inf(self, info):
        """ [internal function] analyze devinfo given by nev_server (response to GET NISCOPEINFO) """
        ## [ find the head ]
        start_idx = info.find('[')
        if start_idx < 0:
            return []
        src = info[start_idx:]
        ## [ scan device list ]
        dev_list = []
        blks = src.split(']')
        for blk in blks:
            if (len(blk) < 2) or (blk[0] != '['):
                continue
            lst = blk[1:].split(',')
            if len(lst) < 6:
                return None
            dev_list.append(nevcl_devNIScope(lst))
        return dev_list

    def _str2list(self, str_data):
        """ [internal function] analyze string_data and convert to a list """
        data = []
        while(True):
            # find the head of the first vector
            start_idx = str_data.find('[')
            if start_idx != 0:
                # register scalars before the first vector
                data.extend(str_data[:start_idx].split())
            if start_idx < 0:
                return data
            # find the tail of the first vector
            tail_idx = str_data.find(']')
            if tail_idx < 0:
                print("! open brace detected")
                return None
            # convert vector expression to list and register
            data.append(str_data[start_idx + 1:tail_idx].split())
            # erase converted string
            str_data = str_data[tail_idx + 1:]

    def _get_server_response(self, comstr, resstr):
        # コマンドを送り、必要なら期待する応答タグを確認する。
        """
        [internal function] send comstr, receive response, and check the response tag
        - response tag check is ommitted if resstr is None
        """
        # send command string
        self.tcpcon.send(comstr)
        # receive reponse
        res = self.tcpcon.recv()
        if res is None:
            return None
        if resstr is None:
            return res
        if res[0] != resstr:
            nevcl_sys.logger.log("unexpected response from server to " + comstr, 2)
            return None
        return res[1:]

    def load_union(self, unionNo):
        """
        load server's union info to self.union_list[unionNo]
        """
        self.load_union_devlist(unionNo)
        self.load_union_channelConf(unionNo)
        self.load_union_dlen(unionNo)
        self.load_union_freq(unionNo)
        self.load_union_trig(unionNo)
       
    def load_union_devlist(self, unionNo):
        self.status = "BUSY"
        # send command and receive data
        comstr = "GET NSU DEVS " + str(unionNo)
        resstr = "#NSUDEVS " + str(unionNo)
        res = self._get_server_response(comstr, resstr)
        if res is None:
            self.status = "IDLE"
            return None
        # find the head of device index list
        info = res[0]
        start_idx = info.find("[")
        if start_idx < 0:
            self.status = "IDLE"
            return None
        # get device number
        self.union_list[unionNo].N_devices = int(info[:start_idx])
        # scan list of channel confs
        blks = info[start_idx:].split("]")
        lst = blks[0][1:].split()  # use space characters for splitting
        if len(lst) < self.union_list[unionNo].N_devices:
            nevcl_sys.logger.log("load_servers_devlist(): device index list too short", 2)
            self.status = "IDLE"
            return None
        self.union_list[unionNo].devices = []
        for val in lst:
            self.union_list[unionNo].devices.append(int(val))
        self.status = "IDLE"
        return len(lst)
        
    def load_union_channelConf(self, unionNo):
        # send command and receive data
        comstr = "GET NSU CHAN " + str(unionNo)
        resstr = "#NSUCHAN " + str(unionNo)
        res = self._get_server_response(comstr, resstr)
        if res is None:
            return None
        # find the head of channel info list
        info = res[0]
        start_idx = info.find("[")
        if start_idx < 0:
            return None
        # get channel number
        self.union_list[unionNo].N_channels = int(info[:start_idx])
        # scan list of channel confs
        src = info[start_idx:]
        ch_idx = 0
        dev_idx = 0
        chvr_list = []
        chvc_list = []
        self.union_list[unionNo].ch_verticalRange = []
        self.union_list[unionNo].ch_verticalCoupling = []
        for blk in src.split("]"):
            if ch_idx >= self.device_list[dev_idx].N_channels:
                self.union_list[unionNo].ch_verticalRange.append(chvr_list)
                self.union_list[unionNo].ch_verticalCoupling.append(chvc_list)
                chvr_list = []
                chvc_list = []
                dev_idx += 1
                ch_idx = 0
            if (len(blk) < 2) or (blk[0] != "["):
                continue
            lst = blk[1:].split()  # use space characters for splitting
            if len(lst) < 2:
                nevcl_sys.logger.log("unexpected response from server (" + blk + ")", 2)
                return None
            chvr_list.append(lst[0])
            chvc_list.append(lst[1])
            ch_idx += 1
        if ch_idx > 0:
            self.union_list[unionNo].ch_verticalRange.append(chvr_list)
            self.union_list[unionNo].ch_verticalCoupling.append(chvc_list)
        return dev_idx

    def load_union_dlen(self, unionNo):
        # send command and receive data
        comstr = "GET NSU DLEN " + str(unionNo)
        resstr = "#NSUDLEN " + str(unionNo)
        res = self._get_server_response(comstr, resstr)
        if res is None:
            return None
        # find the head of actual dlen list
        info = res[0]
        start_idx = info.find("[")
        if start_idx < 0:
            return None
        # get dlen
        self.union_list[unionNo].dlen = int(info[:start_idx])
        # get list of actual dlen
        blks = info[start_idx:].split("]")
        lst = blks[0][1:].split()  # use space characters for splitting
        if len(lst) < self.union_list[unionNo].N_devices:
            nevcl_sys.logger.log("load_servers_dlen(): data length list too short", 2)
            return None
        self.union_list[unionNo].dev_actulaDlen = []
        for val in lst:
            self.union_list[unionNo].dev_actualDlen.append(int(val))
        return len(lst)

    def load_union_freq(self, unionNo):
        # send command and receive data
        comstr = "GET NSU FREQ " + str(unionNo)
        resstr = "#NSUFREQ " + str(unionNo)
        res = self._get_server_response(comstr, resstr)
        if res is None:
            return None
        # find the head of actual dlen list
        info = res[0]
        start_idx = info.find("[")
        if start_idx < 0:
            return None
        # get dlen
        self.union_list[unionNo].freq = float(info[:start_idx])
        # get list of actual dlen
        blks = info[start_idx:].split("]")
        lst = blks[0][1:].split()  # use space characters for splitting
        if len(lst) < self.union_list[unionNo].N_devices:
            nevcl_sys.logger.log("load_servers_dlen(): data length list too short", 2)
            return None
        self.union_list[unionNo].dev_actualFreq = []
        for val in lst:
            self.union_list[unionNo].dev_actualFreq.append(float(val))
        return len(lst)

    def load_union_trig(self, unionNo):
        # send command and receive data
        comstr = "GET NSU TRIG " + str(unionNo)
        resstr = "#NSUTRIG " + str(unionNo)
        res = self._get_server_response(comstr, resstr)
        if res is None:
            return None
        # find the head of actual dlen list
        info = res[0]
        start_idx = info.find("[")
        if start_idx < 0:
            return None
        # get list of trigger configuration
        blks = info[start_idx:].split("]")
        lst = blks[0][1:].split()  # use space characters for splitting
        if len(lst) < 9:  # 9 parameters are expected
            nevcl_sys.logger.log("load_servers_trig(): trigger configuration list too short", 2)
            return None
        self.union_list[unionNo].refPosition = float(lst[0])
        self.union_list[unionNo].triggerType = lst[1]
        self.union_list[unionNo].triggerSource = lst[2]
        self.union_list[unionNo].triggerDevice = int(lst[3])
        self.union_list[unionNo].triggerLevel = float(lst[4])
        self.union_list[unionNo].triggerSlope = lst[5]
        self.union_list[unionNo].triggerCoupling = lst[6]
        self.union_list[unionNo].triggerHoldoff = float(lst[7])
        self.union_list[unionNo].triggerDelay = float(lst[8])
        return len(lst)

    def set_union_devlist(self, unionNo, dev_list):
        # unionに含めるNI-Scope機器のインデックスをサーバーへ設定する。
        # convert dev_list to string
        dl_str = " [ "
        for dev_idx in dev_list:
            dl_str += str(dev_idx) + ' '
        dl_str += ']'
        # send command
        comstr = "SET NSU DEVS " + str(unionNo) + ' ' + str(len(dev_list)) + dl_str
        res = self._get_server_response(comstr, None)
        if res is None:
            return None
        # load server's setting back to self.union_list[unionNo]
        self.load_union_devlist(unionNo)
        return 0

    def set_union_channelConf(self, unionNo, dev_idx, ch_idx,
                              channel_confs: "list of [ range, coupling]"):
        # 対象機器の各チャンネルに入力レンジと結合方式を設定する。
        # convert channel conf to string
        channel_confs_str = ""
        for idx in range(0, ch_idx):
            channel_confs_str += "[ *  * ] "
        for chconf in channel_confs:
            channel_confs_str += "[ " + str(chconf[0]) + " " + str(chconf[1]) + " ] "
        # send command
        comstr = "SET NSU CHAN " + str(unionNo) + ' ' + str(dev_idx) + ' ' + channel_confs_str
        print("# set_union_channelConf()\n  ", comstr)
        res = self._get_server_response(comstr, None)
        if res is None:
            return None
        # load server's setting back to self.union_list[unionNo]
        self.load_union_channelConf(unionNo)
        return 0
            
    def set_union_dlen(self, unionNo, dlen):
        # send command
        comstr = "SET NSU DLEN " + str(unionNo) + ' ' + str(dlen)
        res = self._get_server_response(comstr, None)
        if res is None:
            return None
        # load server's setting back to self.union_list[unionNo]
        self.load_union_dlen(unionNo)
        return 0
            
    def set_union_freq(self, unionNo, freq):
        # send command
        comstr = "SET NSU FREQ " + str(unionNo) + ' ' + str(freq)
        res = self._get_server_response(comstr, None)
        if res is None:
            return None
        # load server's setting back to self.union_list[unionNo]
        self.load_union_freq(unionNo)
        return 0

    def set_union_trig(self, unionNo, trig: "[refPosition, triggerType, triggerSource, triggerDevice, triggerLevel, triggerSlope, triggerCoupling, triggerHoldoff, triggerDelay]"):
        # convert trig configuration to string
        trig_str = "[ "
        for val in trig:
            trig_str += str(val) + ' '
        trig_str += "]"
        # send command
        comstr = "SET NSU TRIG " + str(unionNo) + ' ' + trig_str
        res = self._get_server_response(comstr, None)
        if res is None:
            return None
        # load server's setting back to self.union_list[unionNo]
        self.load_union_trig(unionNo)
        return 0

    def get_psa_stat(self):
        # PSAの取得点数、現在の掃引値、RUNNING等の状態を返す。
        comstr = "GET PSA STAT"
        resstr = "#PSASTAT"
        res = self._get_server_response(comstr, resstr)
        if res is None:
            return None
        lst = res[0].split()  # use space characters for splitting
        if len(lst) < 3:
            nevcl_sys.logger.log("get_psa_status(): status data list too short", 2)
            return None
        return [ int(lst[0]), float(lst[1]), lst[2] ]

    def get_psa_data(self, start, stop):
        # 指定した点番号以降の掃引値とチャンネル波形を取得する。
        # convert range specification to string
        if stop is not None:
            rangespec = str(start)
            if start != stop:
                rangespec += '-' + str(stop)
        else:
            rangespec = str(start) + '-'
        comstr = "GET PSA DATA " + rangespec
        # contact nev_server
        res = self._get_server_response(comstr, None)
        if res is None:
            # #NG応答や通信切断時にack=res[0]で別の例外を起こさない。
            return None
        # check acknowledgement
        ack = res[0]
        resstr = "#PSADATA " + str(start)
        idx = ack.find(resstr)  # if "#PSADATA start_point" is in the acknowledgement resopnse
        if idx < 0:
            nevcl_sys.logger.log("niscope.get_psa_data(): invalid ack: " + ack, 2)
            return None
        last = int(ack[idx + len(resstr):])  # get the last_point
        print("# get_psa_data(): last = " + str(last))
        if last < start:
            return last, []
        # convert data string to list
        data = []
        tag = None
        for str_data in res[1:]:
            l = self._str2list(str_data)
            if(len(l) > 1):
                print("! unexpected data format in PSA DATA")
                return None
            if type(l[0]) is not list:
                if tag is not None:
                    data.append([ tag, dgr ])
                tag = float(l[0])
                dgr = []
            else:
                dgr.append([ float(l[0][idx]) for idx in range(len(l[0])) ])
        if tag is not None:
            data.append([tag, dgr])
        return last, data

    def set_psa(self, unionNo,
                param_conf: "[ AO, device No., channel No.]",
                range_conf: "[ start, stop, steps]",
                fetch_skip: "number of samples to be skipped when fetching data"):
        # AOチャンネルを掃引するPSAを設定する。
        # param_conf = ["AO", device No., channel No.]
        # range_conf = [start, stop, steps]
        if len(param_conf) != 3:
            raise ValueError("param_conf must be ['AO', device_no, channel_no]")
        if str(param_conf[0]).upper() != "AO":
            raise ValueError("Only 'AO' is supported; SAO/DAO/SDO/DDO are not used")
        if len(range_conf) != 3:
            raise ValueError("range_conf must be [start, stop, steps]")
        if int(range_conf[2]) < 1:
            raise ValueError("range_conf steps must be >= 1")
        if int(fetch_skip) < 0:
            raise ValueError("fetch_skip must be >= 0")

        pc_str = '[AO ' + str(int(param_conf[1])) + ' ' + str(int(param_conf[2])) + ']'
        rc_str = '[' + str(range_conf[0]) + ' ' + str(range_conf[1]) + ' ' + str(int(range_conf[2])) + ']'
        comstr = "SET PSA " + str(unionNo) + ' ' + pc_str + ' ' + rc_str + ' ' + str(fetch_skip)
        print("#  set_psa():", comstr)
        res = self._get_server_response(comstr, None)
        if res is None:
            return None
        return 0
    
    def run_psa(self):
        # 設定済みのPSAを開始する。
        res = self._get_server_response("RUN PSA", None)
        if res is None:
            return None
        return 0
    
    def stop_psa(self):
        # 実行中のPSAを停止する。
        res = self._get_server_response("STOP PSA", None)
        if res is None:
            return None
        return 0


class nevcl_uniNIScope():
    """
    Rev. 0.1 (13/09/2017)
    NIScope union information
    """
    ## [ variables ]
    unionNo = 0   # not used for now
    devices = []  # list of indices that refer to nevcl_sysNIScope.device_list
    N_devices = 0
    ch_verticalRange = []
    ch_verticalCoupling = []
    dlen = 0
    freq = 0
    dev_actualDlen = []
    dev_actualFreq = []
    refPosition = 50
    triggerType = "IMMEDIATE"  # IMMEDIATE or EDGE
    triggerSource = "0"
    triggerDevice = 0
    triggerLevel = 0
    triggerSlope = "POSITIVE"  # POSITIVE or NEGATIVE
    triggerCoupling = "DC"  # AC, DC, HF_REJECT, LF_REJECT, AC_PULS_HF_REJECT
    triggerHoldoff = 0
    triggerDelay = 0
    N_channels = 0

    def __init__(self):
        return

    def dump(self):
        print("--")
        print("devices            : ", self.devices)
        print("N_devices          : ", self.N_devices)
        print("ch_verticalRange   : ", self.ch_verticalRange)
        print("ch_verticalCoupling: ", self.ch_verticalCoupling)
        print("N_channels         : ", self.N_channels)
        print("dlen               : ", self.dlen)
        print("dev_actualDlen     : ", self.dev_actualDlen)
        print("freq               : ", self.freq)
        print("dev_actualFreq     : ", self.dev_actualFreq)
        print("refPosition        : ", self.refPosition)
        print("triggerType        : ", self.triggerType)
        print("triggerSource      : ", self.triggerSource)
        print("triggerDevice      : ", self.triggerDevice)
        print("triggerLevel       : ", self.triggerLevel)
        print("triggerSlope       : ", self.triggerSlope)
        print("triggerCoupling    : ", self.triggerCoupling)
        print("triggerHoldoff     : ", self.triggerHoldoff)
        print("triggerDelay       : ", self.triggerDelay)




class nevcl_devNIScope():
    """
    Rev. 0.1 (11/09/2017)
    device information
    """
    ## [ variables ]
    slot = None
    name = None
    model = None
    N_channels = 0
    chassis = None    
    serial = None
    status = None

    def __init__(self, *args):
        """ arg[0]: a member of daqmx device info obtained by self._analyze_inf()
            arg[1]: [ static, analog ]
        """
        if len(args) > 0:
            self.set_devinfo(args[0])
        self.status = 0

    def set_devinfo(self, devinfo):
        if len(devinfo) >= 6:
            self.slot = int(devinfo[0])
            self.name = devinfo[1]
            self.model = devinfo[2]
            self.N_channels = int(devinfo[3])
            self.chassis = int(devinfo[4])
            self.serial = devinfo[5]

    def dump(self):
        print("--")
        print("slot      : ", self.slot)
        print("name      : ", self.name)
        print("model     : ", self.model)
        print("N_channels: ", self.N_channels)
        print("chassis   : ", self.chassis)
        print("serial    : ", self.serial)
        print("status    : ",  self.status)




if __name__ == "__main__":

    import sys
    import nevcl_tcp, nevcl_sys

    #nevcl_logger = nevcl_sys.nevcl_Logger()

    if len(sys.argv) > 1:
        addr = str(sys.argv[1])
    else:
        addr = "localhost"

    tcpc = nevcl_tcp.nevcl_tcpCon(addr)
    niscope_sys = nevcl_sysNIScope(tcpc)

    for dev_info in niscope_sys.device_list:
        dev_info.dump()

    for unionNo in range(niscope_sys.N_unions):
        niscope_sys.union_list[unionNo].dump()

    niscope_sys.set_union_devlist(1, [ 0 ])
    niscope_sys.set_union_channelConf(1, 0, 1, [ [10, "AC"] ])
    niscope_sys.set_union_trig(1, [ 0, 'IMMEDIATE', '*', '*', '*', 'POSITIVE', 'DC', '*', '*'])
    niscope_sys.union_list[1].dump()
    niscope_sys.set_psa(1, [ 'AO', 0, 37 ], [0.0, 0.2, 40], 2500)
    niscope_sys.run_psa()
    while(True):
        res = niscope_sys.get_psa_stat()
        if res[2] == "COMPLETE":
            n, data = niscope_sys.get_psa_data(0, None)
            print(n)
            for datagram in data:
               print(datagram)
            break ;
        if res[2] == "ABORTED" or res[2] == "FAILED":
            print("! PSA failed")
            break ;
            

    #daqmx_sys.set_val_ch("Dev1", 0, [ 0.5 ])
    #daqmx_sys.update()
    #daqmx_sys.print_devinfo("Dev1")
