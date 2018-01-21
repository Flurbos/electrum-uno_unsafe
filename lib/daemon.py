#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2015 Thomas Voegtlin
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import ast
import os
import sys
import time

import jsonrpclib
from jsonrpc import VerifyingJSONRPCServer

from network import Network
from util import to_bytes
from util import json_decode, DaemonThread
from util import print_msg, print_error, print_stderr, to_string
from wallet import WalletStorage, Wallet
from wizard import WizardBase
from commands import known_commands, Commands
from simple_config import SimpleConfig


def get_lockfile(config):
    return os.path.join(config.path, 'daemon')

def remove_lockfile(lockfile):
    os.unlink(lockfile)

def get_fd_or_server(config):
    '''Tries to create the lockfile, using O_EXCL to
    prevent races.  If it succeeds it returns the FD.
    Otherwise try and connect to the server specified in the lockfile.
    If this succeeds, the server is returned.  Otherwise remove the
    lockfile and try again.'''
    lockfile = get_lockfile(config)
    while True:
        try:
            return os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY), None
        except OSError:
            pass
        server = get_server(config)
        if server is not None:
            return None, server
        # Couldn't connect; remove lockfile and try again.
        remove_lockfile(lockfile)

def get_server(config):
    lockfile = get_lockfile(config)
    while True:
        create_time = None
        try:
            with open(lockfile) as f:
                (host, port), create_time = ast.literal_eval(f.read())
                rpc_user, rpc_password = get_rpc_credentials(config)
                if rpc_password == '':
                    server_url = 'http://%s:%d' % (host, port)
                else:
                    server_url = 'http://%s:%s@%s:%d' % (
                        rpc_user, rpc_password, host, port)
                server = jsonrpclib.Server(server_url)
            # Test daemon is running
            server.ping()
            return server
        except:
            pass
        if not create_time or create_time < time.time() - 1.0:
            return None
        # Sleep a bit and try again; it might have just been started
        time.sleep(1.0)


def tobytes(n, length):
    return ''.join(chr((n >> i*8) & 0xff) for i in reversed(range(length)))

def get_rpc_credentials(config):
    rpc_user = config.get('rpcuser', None)
    rpc_password = config.get('rpcpassword', None)
    if rpc_user is None or rpc_password is None:
        rpc_user = 'user'
        import ecdsa, base64
        bits = 128
        nbytes = bits // 8 + (bits % 8 > 0)
        pw_int = ecdsa.util.randrange(pow(2, bits))
        #valuex = ('%%0%dx' % (nbytes << 1) % pw_int).decode('hex')[-nbytes:] '\x00\x00\x00\x00\x00\x00\x07[\xcd\x15'
        valuex = tobytes(pw_int, nbytes)
        pw_b64 = base64.b64encode(valuex, b'-_')
#            pw_int.to_bytes(nbytes, 'big'), b'-_')
        rpc_password = to_string(pw_b64, 'ascii')
        config.set_key('rpcuser', rpc_user)
        config.set_key('rpcpassword', rpc_password, save=True)
    elif rpc_password == '':
        from util import print_stderr
        print_stderr('WARNING: RPC authentication is disabled.')
    return rpc_user, rpc_password

class Daemon(DaemonThread):

    def __init__(self, config, fd):

        DaemonThread.__init__(self)
        self.config = config
        if config.get('offline'):
            self.network = None
        else:
            self.network = Network(config)
            self.network.start()
        self.gui = None
        self.wallets = {}
        # Setup server
        cmd_runner = Commands(self.config, None, self.network)
        host = config.get('rpchost', 'localhost')
        port = config.get('rpcport', 0)
        rpc_user, rpc_password = get_rpc_credentials(config)
        try:
            server = VerifyingJSONRPCServer((host, port), rpc_user=rpc_user,
                               rpc_password=rpc_password, logRequests=False)
        except Exception as e:
            self.print_error('Warning: cannot initialize RPC server on host', host, e)
            self.server = None
            os.close(fd)
            return
        os.write(fd, repr((server.socket.getsockname(), time.time())))
        os.close(fd)
        server.timeout = 0.1
        for cmdname in known_commands:
            server.register_function(getattr(cmd_runner, cmdname), cmdname)
        server.register_function(self.run_cmdline, 'run_cmdline')
        server.register_function(self.ping, 'ping')
        server.register_function(self.run_daemon, 'daemon')
        server.register_function(self.run_gui, 'gui')
        self.server = server
        server.timeout = 0.1
        server.register_function(self.ping, 'ping')

    def ping(self):
        return True

    def run_daemon(self, config):
        sub = config.get('subcommand')
        assert sub in ['start', 'stop', 'status']
        if sub == 'start':
            response = "Daemon already running"
        elif sub == 'status':
            if self.network:
                p = self.network.get_parameters()
                response = {
                    'path': self.network.config.path,
                    'server': p[0],
                    'blockchain_height': self.network.get_local_height(),
                    'server_height': self.network.get_server_height(),
                    'nodes': self.network.get_interfaces(),
                    'connected': self.network.is_connected(),
                    'auto_connect': p[4],
                    'wallets': {k: w.is_up_to_date()
                                for k, w in self.wallets.items()},
                }
            else:
                response = "Daemon offline"
        elif sub == 'stop':
            self.stop()
            response = "Daemon stopped"
        return response

    def run_gui(self, config_options):
        config = SimpleConfig(config_options)
        if self.gui:
            if hasattr(self.gui, 'new_window'):
                path = config.get_wallet_path()
                self.gui.new_window(path, config.get('url'))
                response = "ok"
            else:
                response = "error: current GUI does not support multiple windows"
        else:
            response = "Error: Electrum is running in daemon mode. Please stop the daemon first."
        return response

    def load_wallet(self, path, get_wizard=None):
        if path in self.wallets:
            wallet = self.wallets[path]
        else:
            storage = WalletStorage(path)
            if get_wizard:
                if storage.file_exists:
                    wallet = Wallet(storage)
                    action = wallet.get_action()
                else:
                    action = 'new'
                if action:
                    wizard = get_wizard()
                    wallet = wizard.run(self.network, storage)
                else:
                    wallet.start_threads(self.network)
            else:
                wallet = Wallet(storage)
                wallet.start_threads(self.network)
            if wallet:
                self.wallets[path] = wallet
        return wallet

    def run_cmdline(self, config_options):
        config = SimpleConfig(config_options)
        cmdname = config.get('cmd')
        cmd = known_commands[cmdname]
        path = config.get_wallet_path()
        wallet = self.load_wallet(path) if cmd.requires_wallet else None
        # arguments passed to function
        args = map(lambda x: config.get(x), cmd.params)
        # decode json arguments
        args = map(json_decode, args)
        # options
        args += map(lambda x: config.get(x), cmd.options)
        cmd_runner = Commands(config, wallet, self.network,
                              password=config_options.get('password'),
                              new_password=config_options.get('new_password'))
        func = getattr(cmd_runner, cmd.name)
        result = func(*args)
        return result

    def run(self):
        while self.is_running():
            self.server.handle_request() if self.server else time.sleep(0.1)
        for k, wallet in self.wallets.items():
            wallet.stop_threads()
        if self.network:
            self.print_error("shutting down network")
            self.network.stop()
            self.network.join()

    def stop(self):
        self.print_error("stopping, removing lockfile")
        remove_lockfile(get_lockfile(self.config))
        DaemonThread.stop(self)

    def init_gui(self, config, plugins):
        gui_name = config.get('gui', 'qt')
        if gui_name in ['lite', 'classic']:
            gui_name = 'qt'
        gui = __import__('electrum_gui.' + gui_name, fromlist=['electrum_gui'])
        self.gui = gui.ElectrumGui(config, self, plugins)
        self.gui.main()
