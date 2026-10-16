#!/usr/bin/python3
"""NEVサーバーとのTCP通信層。

アプリ既定のポートは9000。通信形式の変更はこのファイルの send/recv と、
機能別の nevcl_daqmx.py / nevcl_niscope.py を確認する。
"""
#
# tcp connection module
# Takashi Kohno
# Rev. 0.1,  18/06/2017

import select
import socket
import sys

class nevcl_tcpCon():
    """TCPソケットを保持し、サーバーの#OK/#NG終端パケットを読み書きする。"""

    # configuration
    ipaddr = "localhost"
    port = 9000
    timeout = 5  # blocking time in select.select()
    chunk_size = 8192
    retry_count = 5
    debug_mode = False

    # variables/buffers
    sock = None
    resbuf = ""

    # dummy connection (for debug)
    dummy_mode = False
    recv_data = [ "#DAQMXINFO",
                  "SAO[1,Dev2,PXI-6704,16,1,0.000000,0][2,Dev3,PXI-6704,16,1,0.000000,0]",
                  "DAO[0,Dev1,PXI-6733,8,1024,100000.000000,0][3,Dev4,PXI-6733,8,1024,100000.000000,0]",
                  "SDO[1,Dev2,PXI-6704,8,1,0.000000,0][2,Dev3,PXI-6704,8,1,0.000000,0]" ]
    recv_data_raw = "#DAQMXINFO\nSAO[6,DACS0,PXI-6704,16,1,0.000000,0][7,DACS1,PXI-6704,16,1,0.000000,0]\nDAO[0,DACD0,PXI-6733,8,1024,100000.000000,0][1,DACD1,PXI-6733,8,1024,100000.000000,0][2,DACD2,PXI-6733,8,1024,100000.000000,0][3,DACD3,PXI-6733,8,1024,100000.000000,0][4,DACD4,PXI-6733,8,1024,100000.000000,0][5,DACD5,PXI-6733,8,1024,100000.000000,0]\nSDO[6,DACS0,PXI-6704,8,1,0.000000,0][7,DACS1,PXI-6704,8,1,0.000000,0]\n"

    def __init__(self, addr, *args):
        if len(args) > 0:
            self.port = args[0]
        self.ipaddr = addr
        self.connect()
        self.resbuf = ""

    def connect(self):
        """ connect to the server """
        if self.dummy_mode:
            return
        if self.sock is not None:
            self.sock.close()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_address = (self.ipaddr, self.port)
        try:
            self.sock.connect(server_address)
        except ConnectionRefusedError:
            print("! connection refused")

    def send(self, string):
        """ send a string to the server """
        if self.dummy_mode:
            print(string)
        else:
            retry = self.retry_count
            while True:
                line = string.encode('utf-8')
                try:
                    self.sock.sendall(line)
                except BrokenPipeError:
                    if retry == 0:
                        nevcl_logger("! TCP connection could not be recovered.") ;
                        return
                    retry -= 1
                    nevcl_logger("! TCP connection has been broken. Trying reconnection...")
                    self.connect()
                    continue
                break

    def _recv(self):
        """ [internal function] try to receive data from socket for self.retry_count times """
        retry = self.retry_count
        while True:
            rawdata = self.sock.recv(self.chunk_size)
            if not rawdata:
                if retry == 0:
                    nevcl_logger.log("! TCP connection could not be recovered.")
                    return None
                retry -= 1
                nevcl_logger.log("! TCP connection has been broken. Trying reconnection...")
                self.connect()
                continue
            break
        return rawdata.decode('utf-8')
            
    def recv_raw(self):
        # #OK または #NG まで受信し、次の応答に続く余剰データはバッファに残す。
        """ data receiver (blocking)
        - returns a data block separated by "#OK"
        - returns None if connection has been killed
        """
        if self.dummy_mode:
            return self.recv_data_raw
        while True:
            readable, writable, exceptional = select.select([self.sock], [], [], self.timeout)
            if len(readable) > 0:
                break ;
        buf = self.resbuf  # load the residual data in the previous session
        if self.debug_mode:
            print("# recv_raw(): resbuf =", buf)
        while True:
            data = self._recv()
            if data is None:
                return None, None
            if self.debug_mode:
                print("# recv_raw(): data =", data)
            idx = data.find("#OK")  # find termination code
            if idx >= 0:  # termination code found
                buf = buf + data[:idx]
                if len(data) > idx + 3:
                    residual = data[idx + 3:]
                    if residual[0] == '\n':
                        residual = residual[1:]
                    self.resbuf = residual[:]
                return buf, None
            idx = data.find("#NG") # find error code
            if idx >= 0:  # error code found
                buf = buf + data[:idx]
                if len(data) > idx + 3:
                    residual = data[idx + 3:]
                    while True:
                        idx = residual.find("\n")
                        if idx >= 0:  # end of error message found
                            self.resbuf = residual[idx + 1:]
                            return buf, residual[:idx]
                        residual += self._recv()
            buf = buf + data  # just add newly read data if no termination code found

    def recv(self):
        # 生データを改行区切りの文字列配列へ整形して返す。
        """ cooked data receiver (blocking)
            - returns an array of string (each element is separated by \n in the original string)
            - returns None if connection has been killed
            - uses self.recv_raw()
        """
        #if self.dummy_mode:
        #    return self.recv_data
        if self.debug_mode:
            print("# nevl_tcpCon.recv()")
        data, error = self.recv_raw()
        if self.debug_mode:
            print("  [" + data + "]")
        if error is not None:
            print("! error: " + error)
            return None
        packet = []
        while True:
            idx = data.find("\n")  # find separator code
            if idx < 0:
                packet.append(data.strip())
                return packet
            packet.append(data[:idx].strip())
            if idx == len(data) - 1:
                return packet
            data = data[idx + 1:]
        
    def close(self):
        # 接続済みソケットを閉じる。
        """ close connection """
        if self.dummy_mode:
            return
        self.sock.close()
