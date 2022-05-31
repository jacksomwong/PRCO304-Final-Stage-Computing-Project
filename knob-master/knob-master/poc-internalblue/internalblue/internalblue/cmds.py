#!/usr/bin/env python2

# cmds.py
#
# All available CLI commands are defined in this file by
# creating subclasses of the Cmd class.
#
# Copyright (c) 2018 Dennis Mantz. (MIT License)
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
# - The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
# - The Software is provided "as is", without warranty of any kind, express or
#   implied, including but not limited to the warranties of merchantability,
#   fitness for a particular purpose and noninfringement. In no event shall the
#   authors or copyright holders be liable for any claim, damages or other
#   liability, whether in an action of contract, tort or otherwise, arising from,
#   out of or in connection with the Software or the use or other dealings in the
#   Software.

from pwn import *
import os
import sys
import Queue
import inspect
import argparse
import subprocess
from threading import Timer
import textwrap
import struct
import time
import select

def getCmdList():
    """ Returns a list of all commands which are defined in this cmds.py file.
    This is done by searching for all subclasses of Cmd
    """
    return [obj for name, obj in inspect.getmembers(sys.modules[__name__]) 
                            if inspect.isclass(obj) and issubclass(obj, Cmd)][1:]

def findCmd(keyword):
    """ Find and return a Cmd subclass for a given keyword.
    """
    command_list = getCmdList()
    matching_cmds = [cmd for cmd in command_list if keyword in cmd.keywords]
    if(len(matching_cmds) == 0):
        return None
    if(len(matching_cmds) > 1):
        log.warn("Multiple commands match: " + str(matching_cmds))
        return None
    return matching_cmds[0]

def auto_int(x):
    """ Convert a string (either decimal number or hex number) into an integer.
    """
    return int(x, 0)

def bt_addr_to_str(bt_addr):
    """ Convert a Bluetooth address (6 bytes) into a human readable format.
    """
    return ":".join([b.encode("hex") for b in bt_addr])


class Cmd:
    """ This class is the superclass of a CLI command. Every CLI command
    must be defined as subclass of Cmd. The subclass must define the
    'keywords' list as member variable. The actual implementation of the
    command should be located in the work() method.
    """
    keywords = []

    memory_image = None
    memory_image_template_filename = "_memdump_template.bin"

    def __init__(self, cmdline, internalblue):
        self.cmdline = cmdline
        self.internalblue = internalblue

    def __str__(self):
        return self.cmdline

    def work(self):
        return True

    def abort_cmd(self):
        self.aborted = True
        if hasattr(self, 'progress_log'):
            self.progress_log.failure("Command aborted")

    def getArgs(self):
        try:
            return self.parser.parse_args(self.cmdline.split(' ')[1:])
        except SystemExit:
            return None

    def isAddressInSections(self, address, length=0, sectiontype=""):
        for section in self.internalblue.fw.SECTIONS:
            if (sectiontype.upper() == "ROM" and not section.is_rom) or (sectiontype.upper() == "RAM" and not section.is_ram):
                continue

            if(address >= section.start_addr and address <= section.end_addr):
                if(address + length <= section.end_addr):
                    return True
                else:
                    return False
        return False

    def readMem(self, address, length, progress_log=None, bytes_done=0, bytes_total=0):
        return self.internalblue.readMem(address, length, progress_log, bytes_done, bytes_total)

    def writeMem(self, address, data, progress_log=None, bytes_done=0, bytes_total=0):
        return self.internalblue.writeMem(address, data, progress_log, bytes_done, bytes_total)

    def initMemoryImage(self):
        bytes_done = 0
        if(not os.path.exists(self.memory_image_template_filename)):
            log.info("No template found. Need to read ROM sections as well!")
            bytes_total = sum([s.size() for s in self.internalblue.fw.SECTIONS])
            self.progress_log = log.progress("Initialize internal memory image")
            dumped_sections = {}
            for section in self.internalblue.fw.SECTIONS:
                dumped_sections[section.start_addr] = self.readMem(section.start_addr, section.size(), self.progress_log, bytes_done, bytes_total)
                bytes_done += section.size()
            self.progress_log.success("Received Data: complete")
            Cmd.memory_image = fit(dumped_sections, filler='\x00')
            f = open(self.memory_image_template_filename, 'wb')
            f.write(Cmd.memory_image)
            f.close()
        else:
            log.info("Template found. Only read non-ROM sections!")
            Cmd.memory_image = read(self.memory_image_template_filename)
            self.refreshMemoryImage()

    def refreshMemoryImage(self):
        bytes_done = 0
        bytes_total = sum([s.size() for s in self.internalblue.fw.SECTIONS if not s.is_rom])
        self.progress_log = log.progress("Refresh internal memory image")
        for section in self.internalblue.fw.SECTIONS:
            if not section.is_rom:
                sectiondump = self.readMem(section.start_addr, section.size(), self.progress_log, bytes_done, bytes_total)
                Cmd.memory_image = Cmd.memory_image[0:section.start_addr] + sectiondump + Cmd.memory_image[section.end_addr:]
                bytes_done += section.size()
        self.progress_log.success("Received Data: complete")

    def getMemoryImage(self, refresh=False):
        if Cmd.memory_image == None:
            self.initMemoryImage()
        elif refresh:
            self.refreshMemoryImage()
        return Cmd.memory_image

    def launchRam(self, address):
        return self.internalblue.launchRam(address)




#
# Start of implemented commands:
#

class CmdHelp(Cmd):
    keywords = ['help', '?']
    description = "Display available commands. Use help <cmd> to display command specific help."

    def work(self):
        args = self.cmdline.split(' ')
        command_list = getCmdList()
        if(len(args) > 1):
            cmd = findCmd(args[1])
            if cmd == None:
                log.info("No command with the name: " + args[1])
                return True
            if hasattr(cmd,'parser'):
                cmd.parser.print_help()
            else:
                print(cmd.description)
                print("Aliases: " + " ".join(cmd.keywords))
        else:
            for cmd in command_list:
                print(cmd.keywords[0].ljust(15) + 
                        ("\n" + " "*15).join(textwrap.wrap(cmd.description, 60)))
        return True

class CmdExit(Cmd):
    keywords = ['exit', 'quit', 'q', 'bye']
    description = "Exit the program."

    def work(self):
        self.internalblue.exit_requested = True
        return True

class CmdLogLevel(Cmd):
    keywords = ['log_level', 'loglevel', 'verbosity']
    description = "Change the verbosity of log messages."
    log_levels = ['CRITICAL', 'DEBUG', 'ERROR', 'INFO', 'NOTSET', 'WARN', 'WARNING']
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("level",
                        help="New log level (%s)" % ", ".join(log_levels))

    def work(self):
        args = self.getArgs()
        if args==None:
            return True
        loglevel = args.level
        if(loglevel.upper() in self.log_levels):
            context.log_level = loglevel
            self.internalblue.log_level = loglevel
            log.info("New log level: " + str(context.log_level))
            return True
        else:
            log.warn("Not a valid log level: " + loglevel)
            return False

class CmdMonitor(Cmd):
    keywords = ['monitor']
    description = "Controlling the LMP monitor."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("type", 
                        help="One of: hci, lmp")
    parser.add_argument("command", 
                        help="One of: start, status, stop, kill")

    class MonitorController:
        hciInstance = None
        lmpInstance = None

        @staticmethod
        def getMonitorController(name, internalblue):
            if name == "hci":
                if CmdMonitor.MonitorController.hciInstance == None:
                    #Encapsulation type: Bluetooth H4 with linux header (99) None:
                    CmdMonitor.MonitorController.hciInstance = CmdMonitor.MonitorController.__MonitorController(internalblue, 0xC9)
                    CmdMonitor.MonitorController.hciInstance.startMonitor = CmdMonitor.MonitorController.hciInstance.startHciMonitor
                    CmdMonitor.MonitorController.hciInstance.stopMonitor  = CmdMonitor.MonitorController.hciInstance.stopHciMonitor
                    CmdMonitor.MonitorController.hciInstance._callback    = CmdMonitor.MonitorController.hciInstance.hciCallback
                return CmdMonitor.MonitorController.hciInstance
            elif name == "lmp":
                if CmdMonitor.MonitorController.lmpInstance == None:
                    # TODO: pcap data link type should be 255
                    # see: https://github.com/greatscottgadgets/ubertooth/wiki/Bluetooth-Captures-in-PCAP#linktype_bluetooth_bredr_bb
                    CmdMonitor.MonitorController.lmpInstance = CmdMonitor.MonitorController.__MonitorController(internalblue, 0x01)
                    CmdMonitor.MonitorController.lmpInstance.startMonitor = CmdMonitor.MonitorController.lmpInstance.startLmpMonitor
                    CmdMonitor.MonitorController.lmpInstance.stopMonitor  = CmdMonitor.MonitorController.lmpInstance.stopLmpMonitor
                    CmdMonitor.MonitorController.lmpInstance._callback    = CmdMonitor.MonitorController.lmpInstance.lmpCallback
                return CmdMonitor.MonitorController.lmpInstance
            else:
                return None

        class __MonitorController:
            def __init__(self, internalblue, pcap_data_link_type):
                self.internalblue = internalblue
                self.running = False
                self.wireshark_process = None
                self.poll_timer = None
                self.pcap_data_link_type = pcap_data_link_type
            
            def _spawnWireshark(self):
                # Global Header Values
                PCAP_GLOBAL_HEADER_FMT = '@ I H H i I I I '
                PCAP_MAGICAL_NUMBER = 2712847316
                PCAP_MJ_VERN_NUMBER = 2
                PCAP_MI_VERN_NUMBER = 4
                PCAP_LOCAL_CORECTIN = 0
                PCAP_ACCUR_TIMSTAMP = 0
                PCAP_MAX_LENGTH_CAP = 65535
                PCAP_DATA_LINK_TYPE = self.pcap_data_link_type

                pcap_header = struct.pack('@ I H H i I I I ',
                        PCAP_MAGICAL_NUMBER,
                        PCAP_MJ_VERN_NUMBER,
                        PCAP_MI_VERN_NUMBER,
                        PCAP_LOCAL_CORECTIN,
                        PCAP_ACCUR_TIMSTAMP,
                        PCAP_MAX_LENGTH_CAP,
                        PCAP_DATA_LINK_TYPE)

                self.wireshark_process = subprocess.Popen(
                        ["wireshark", "-k", "-i", "-"], 
                        stdin=subprocess.PIPE)

                self.wireshark_process.stdin.write(pcap_header)

                self.poll_timer = Timer(3, self._pollTimer, ())
                self.poll_timer.start()

            def _pollTimer(self):
                if self.running and self.wireshark_process != None:
                    if self.wireshark_process.poll() == 0:
                        # Process has ended
                        log.debug("_pollTimer: Wireshark has terminated")
                        self.stopMonitor()
                        self.wireshark_process = None
                    else:
                        # schedule new timer
                        self.poll_timer = Timer(3, self._pollTimer, ())
                        self.poll_timer.start()

            def startHciMonitor(self):
                if self.running:
                    log.warn("HCI Monitor already running!")
                    return False

                self.running = True
                if self.wireshark_process == None:
                    self._spawnWireshark()

                self.internalblue.registerHciCallback(self._callback)
                log.info("HCI Monitor started.")
                return True

            def stopHciMonitor(self):
                if not self.running:
                    log.warn("HCI Monitor is not running!")
                    return False
                self.internalblue.unregisterHciCallback(self._callback)
                self.running = False
                log.info("HCI Monitor stopped.")
                return True

            def startLmpMonitor(self):
                if self.running:
                    log.warn("LMP Monitor already running!")
                    return False

                self.running = True
                if self.wireshark_process == None:
                    self._spawnWireshark()

                self.internalblue.startLmpMonitor(self._callback)
                log.info("LMP Monitor started.")
                return True

            def stopLmpMonitor(self):
                if not self.running:
                    log.warn("LMP Monitor is not running!")
                    return False
                self.internalblue.stopLmpMonitor()
                self.running = False
                log.info("LMP Monitor stopped.")
                return True

            def killMonitor(self):
                if self.running:
                    self.stopMonitor()
                if self.poll_timer != None:
                    self.poll_timer.cancel()
                    self.poll_timer = None
                if self.wireshark_process != None:
                    log.info("Killing Wireshark process...")
                    try:
                        self.wireshark_process.terminate()
                        self.wireshark_process.wait()
                    except OSError:
                        log.warn("Error during wireshark process termination")
                    self.wireshark_process = None
                    

            def getStatus(self):
                return self.running

            def hciCallback(self, record):
                hcipkt, orig_len, inc_len, flags, drops, recvtime = record

                dummy = "\x00\x00\x00"      # TODO: Figure out purpose of these fields
                direction = p8(flags & 0x01)
                packet = dummy + direction + hcipkt.getRaw()
                length = len(packet)
                ts_sec =  recvtime.second #+ timestamp.minute*60 + timestamp.hour*60*60 #FIXME timestamp not set
                ts_usec = recvtime.microsecond
                pcap_packet = struct.pack('@ I I I I', ts_sec, ts_usec, length, length) + packet
                try:
                    self.wireshark_process.stdin.write(pcap_packet)
                    self.wireshark_process.stdin.flush()
                    log.debug("HciMonitorController._callback: done")
                except IOError as e:
                    log.warn("HciMonitorController._callback: broken pipe. terminate.")
                    self.killMonitor()

            def lmpCallback(self, lmp_packet, sendByOwnDevice, src, dest, timestamp):
                eth_header = dest + src + "\xff\xf0"
                meta_data  = "\x00"*6 if sendByOwnDevice else "\x01\x00\x00\x00\x00\x00"
                packet_header = "\x19\x00\x00" + p8(len(lmp_packet)<<3 | 7)

                packet = eth_header + meta_data + packet_header + lmp_packet
                packet += "\x00\x00" # CRC
                length = len(packet)
                ts_sec =  timestamp.second + timestamp.minute*60 + timestamp.hour*60*60
                ts_usec = timestamp.microsecond
                pcap_packet = struct.pack('@ I I I I', ts_sec, ts_usec, length, length) + packet
                try:
                    self.wireshark_process.stdin.write(pcap_packet)
                    self.wireshark_process.stdin.flush()
                    log.debug("LmpMonitorController._callback: done")
                except IOError as e:
                    log.warn("LmpMonitorController._callback: broken pipe. terminate.")
                    self.killMonitor()


    def work(self):
        args = self.getArgs()
        if args==None:
            return True

        monitorController = CmdMonitor.MonitorController.getMonitorController(args.type, self.internalblue)
        if monitorController == None:
            log.warn("Unknown monitor type: " + args.type)
            return False

        if args.command == "start":
            monitorController.startMonitor()
        elif args.command == "status":
            log.info("LMP Monitor is %s." % ("running" if monitorController.getStatus() else "not running"))
        elif args.command == "stop":
            monitorController.stopMonitor()
        elif args.command == "kill":
            monitorController.killMonitor()
        else:
            log.warn("Unknown subcommand: " + args.command)
            return False
        return True


class CmdRepeat(Cmd):
    keywords = ['repeat']
    description = "Repeat a given command until user stops it."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("timeout", type=int,
                        help="idle time (in milliseconds) between repetitions.")
    parser.add_argument("command", 
                        help="Command which shall be repeated.")

    def work(self):
        args = self.cmdline.split(" ")
        if len(args) < 3:
            log.info("Need more arguments!")
            return False

        try:
            timeout = int(args[1])
        except ValueError:
            log.info("Not a number: " + args[1])
            return False

        repcmdline = " ".join(args[2:])
        cmdclass = findCmd(args[2])

        if cmdclass == None:
            log.warn("Unknown command: " + args[2])
            return False

        while True:
            # Check for keypresses by user:
            if select.select([sys.stdin],[],[],0.0)[0]:
                log.info("Repeat aborted by user!")
                return True

            # instanciate and run cmd
            cmd_instance = cmdclass(repcmdline, self.internalblue)
            if(not cmd_instance.work()):
                log.warn("Command failed: " + str(cmd_instance))
                return False
            time.sleep(timeout*0.001)
            

class CmdDumpMem(Cmd):
    keywords = ['dumpmem', 'memdump']
    description = "Dumps complete memory image into a file."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--norefresh", "-n", action="store_true",
                        help="Do not refresh internal memory image before dumping to file.")
    parser.add_argument("--ram", "-r", action="store_true",
                        help="Only dump the two RAM sections.")
    parser.add_argument("--file", "-f", default="memdump.bin",
                        help="Filename of memory dump (default: %(default)s)")

    def work(self):
        args = self.getArgs()
        if args==None:
            return True

        if args.ram:
            bytes_total = sum([s.size() for s in self.sections if s.is_ram])
            bytes_done = 0
            self.progress_log = log.progress("Downloading RAM sections...")
            for section in filter(lambda s: s.is_ram, self.sections):
                filename = args.file + "_" + hex(section.start_addr)
                if(os.path.exists(filename)):
                    if not yesno("Overwrite '%s'?" % filename):
                        log.info("Skipping section @%s" % hex(section.start_addr))
                        bytes_done += section.size()
                        continue
                ram = self.readMem(section.start_addr, section.size(), self.progress_log, bytes_done, bytes_total)
                f = open(filename, "wb")
                f.write(ram)
                f.close()
                bytes_done += section.size()
            self.progress_log.success("Done")
            return True

        if(os.path.exists(args.file)):
            if not yesno("Overwrite '%s'?" % os.path.abspath(args.file)):
                return False
        
        dump = self.getMemoryImage(refresh=not args.norefresh)
        f = open(args.file, 'wb')
        f.write(dump)
        f.close()
        log.info("Memory dump saved in '%s'!" % os.path.abspath(args.file))
        return True

class CmdSearchMem(Cmd):
    keywords = ['searchmem', 'memsearch']
    description = "Search a pattern (string or hex) in the memory image."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--refresh", "-r", action="store_true",
                        help="Refresh internal memory image before searching.")
    parser.add_argument("--hex", action="store_true",
                        help="Interpret pattern as hex string (e.g. ff000a20...)")
    parser.add_argument("--address", "-a", action="store_true",
                        help="Interpret pattern as address (hex)")
    parser.add_argument("--context", "-c", type=auto_int, default=0,
                        help="Length of the hexdump before and after the matching pattern (default: %(default)s).")
    parser.add_argument("pattern", nargs='*',
                        help="Search Pattern")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        pattern = ' '.join(args.pattern)
        highlight = pattern
        if args.hex:
            try:
                pattern = pattern.decode('hex')
                highlight = pattern
            except TypeError as e:
                log.warn("Search pattern cannot be converted to hexstring: " + str(e))
                return False
        elif args.address:
            pattern = p32(int(pattern, 16))
            highlight = [x for x in pattern if x != '\x00']

        memimage = self.getMemoryImage(refresh=args.refresh)
        matches = [m.start(0) for m in re.finditer(re.escape(pattern), memimage)]

        hexdumplen = (len(pattern) + 16) & 0xFFFF0
        for match in matches:
            startadr = (match & 0xFFFFFFF0) - args.context
            endadr = (match+len(pattern)+16 & 0xFFFFFFF0) + args.context
            log.info("Match at 0x%08x:" % match)
            log.hexdump(memimage[startadr:endadr], begin=startadr, highlight=highlight)
        return True

class CmdHexdump(Cmd):
    keywords = ['hexdump', 'hd']
    description = "Display a hexdump of a specified region in the memory."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--length", "-l", type=auto_int, default=256,
                        help="Length of the hexdump (default: %(default)s).")
    parser.add_argument("--aligned", "-a", action="store_true",
                        help="Access the memory strictly 4-byte aligned.")
    parser.add_argument("address", type=auto_int,
                        help="Start address of the hexdump.")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        #if not self.isAddressInSections(args.address, args.length):
        #    answer = yesno("Warning: Address 0x%08x (len=0x%x) is not inside a valid section. Continue?" % (args.address, args.length))
        #    if not answer:
        #        return False

        dump = None
        if args.aligned:
            dump = self.internalblue.readMemAligned(args.address, args.length)
        else:
            dump = self.readMem(args.address, args.length)

        if dump == None:
            return False

        log.hexdump(dump, begin=args.address)
        return True

class CmdTelescope(Cmd):
    keywords = ['telescope', 'tel']
    description = "Display a specified region in the memory and follow pointers to valid addresses."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--length", "-l", type=auto_int, default=64,
                        help="Length of the telescope dump (default: %(default)s).")
    parser.add_argument("address", type=auto_int,
                        help="Start address of the telescope dump.")

    def telescope(self, data, depth):
        val = u32(data[0:4])
        if val == 0:
            return [val, '']
        if(depth > 0 and self.isAddressInSections(val,0x20)):
            newdata = self.readMem(val, 0x20)
            recursive_result = self.telescope(newdata, depth-1)
            recursive_result.insert(0, val)
            return recursive_result
        else:
            s = ''
            for c in data:
                if isprint(c):
                    s += c
                else:
                    break
            return [val, s]

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        if not self.isAddressInSections(args.address, args.length):
            answer = yesno("Warning: Address 0x%08x (len=0x%x) is not inside a valid section. Continue?" % (args.address, args.length))
            if not answer:
                return False

        dump = self.readMem(args.address, args.length + 4)
        if dump == None:
            return False

        for index in range(0, len(dump)-4, 4):
            chain = self.telescope(dump[index:], 4)
            output = "0x%08x: " % (args.address+index)
            output += ' -> '.join(["0x%08x" % x for x in chain[:-1]])
            output += ' \"' + chain[-1] + '"'
            log.info(output)
        return True

class CmdDisasm(Cmd):
    keywords = ['disasm', 'disas', 'disassemble', 'd']
    description = "Display a disassembly of a specified region in the memory."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--length", "-l", type=auto_int, default=128,
                        help="Length of the disassembly (default: %(default)s).")
    parser.add_argument("address", type=auto_int,
                        help="Start address of the disassembly.")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        if not self.isAddressInSections(args.address, args.length):
            answer = yesno("Warning: Address 0x%08x (len=0x%x) is not inside a valid section. Continue?" % (args.address, args.length))
            if not answer:
                return False

        dump = self.readMem(args.address, args.length)

        if dump == None:
            return False

        print(disasm(dump, vma=args.address))
        return True

class CmdWriteMem(Cmd):
    keywords = ['writemem']
    description = "Writes data to a specified memory address."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--hex", action="store_true",
                        help="Interpret data as hex string (e.g. ff000a20...)")
    parser.add_argument("--int", action="store_true",
                        help="Interpret data as 32 bit integer (e.g. 0x123)")
    parser.add_argument("--file", "-f",
                        help="Read data from this file instead.")
    parser.add_argument("--repeat", "-r", default=1, type=auto_int,
                        help="Number of times to repeat the data (default: %(default)s)")
    parser.add_argument("address", type=auto_int,
                        help="Destination address") 
    parser.add_argument("data", nargs="*",
                        help="Data as string (or hexstring/integer, see --hex, --int)")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        if args.file != None:
            data = read(args.file)
        elif len(args.data) > 0:
            data = ' '.join(args.data)
            if args.hex:
                try:
                    data = data.decode('hex')
                except TypeError as e:
                    log.warn("Data string cannot be converted to hexstring: " + str(e))
                    return False
            elif args.int:
                data = p32(auto_int(data))
        else:
            self.parser.print_usage()
            print("Either data or --file is required!")
            return False

        data = data * args.repeat

        if not self.isAddressInSections(args.address, len(data), sectiontype="RAM"):
            answer = yesno("Warning: Address 0x%08x (len=0x%x) is not inside a RAM section. Continue?" % (args.address, len(args.data)))
            if not answer:
                return False

        self.progress_log = log.progress("Writing Memory")
        if self.writeMem(args.address, data, self.progress_log, bytes_done=0, bytes_total=len(data)):
            self.progress_log.success("Written %d bytes to 0x%08x." % (len(data), args.address))
            return True
        else:
            self.progress_log.failure("Write failed!")
            return False

class CmdWriteAsm(Cmd):
    keywords = ['writeasm', 'asm']
    description = "Writes assembler instructions to a specified memory address."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--dry", "-d", action="store_true",
                        help="Only pass code to the assembler but don't write to memory")
    parser.add_argument("--file", "-f",
                        help="Open file in text editor, then read assembly from this file.")
    parser.add_argument("address", type=auto_int,
                        help="Destination address") 
    parser.add_argument("code", nargs="*",
                        help="Assembler code as string")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        if args.file != None:
            if(not os.path.exists(args.file)):
                f = open(args.file, "w")
                f.write("/* Write arm thumb code here.\n")
                f.write("   Use '@' or '//' for single line comments or C-like block comments. */\n")
                f.write("\n// 0x%08x:\n\n" % args.address)
                f.close()

            editor = os.environ.get("EDITOR", "vim")
            subprocess.call([editor, args.file])

            code = read(args.file)
        elif len(args.code) > 0:
            code = ' '.join(args.code)
        else:
            self.parser.print_usage()
            print("Either code or --file is required!")
            return False

        try:
            data = asm(code, vma=args.address)
        except PwnlibException:
            return False

        if len(data)>0:
            log.info("Assembler was successful. Machine code (len = %d bytes) is:" % len(data))
            log.hexdump(data, begin=args.address)
        else:
            log.info("Assembler didn't produce any machine code.")
            return False

        if(args.dry):
            log.info("This was a dry run. No data written to memory!")
            return True

        if not self.isAddressInSections(args.address, len(data), sectiontype="RAM"):
            answer = yesno("Warning: Address 0x%08x (len=0x%x) is not inside a RAM section. Continue?" % (args.address, len(data)))
            if not answer:
                return False

        self.progress_log = log.progress("Writing Memory")
        if self.writeMem(args.address, data, self.progress_log, bytes_done=0, bytes_total=len(data)):
            self.progress_log.success("Written %d bytes to 0x%08x." % (len(data), args.address))
            return True
        else:
            self.progress_log.failure("Write failed!")
            return False


class CmdExec(Cmd):
    keywords = ['exec', 'execute']
    description = "Writes assembler instructions to RAM and jumps there."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--dry", "-d", action="store_true",
                        help="Only pass code to the assembler but don't write to memory and don't execute")
    parser.add_argument("--edit", "-e", action="store_true",
                        help="Edit command before execution")
    parser.add_argument("--addr", "-a", type=auto_int, default=0x211800,
                        help="Destination address of the command instructions") 
    parser.add_argument("cmd",
                        help="Name of the command to execute (corresponds to file exec_<cmd>.s)")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        filename = "exec_%s.s" % args.cmd
        if not os.path.exists(filename):
            f = open(filename, "w")
            f.write("/* Write arm thumb code here.\n")
            f.write("   Use '@' or '//' for single line comments or C-like block comments. */\n")
            f.write("\n// Default destination address is 0x%08x:\n\n" % args.addr)
            f.close()
            args.edit = True

        if args.edit:
            editor = os.environ.get("EDITOR", "vim")
            subprocess.call([editor, filename])

        code = read(filename)

        try:
            data = asm(code, vma=args.addr)
        except PwnlibException:
            return False

        if len(data)==0:
            log.info("Assembler didn't produce any machine code.")
            return False

        if args.edit:
            log.info("Assembler was successful. Machine code (len = %d bytes) is:" % len(data))
            log.hexdump(data, begin=args.addr)

        if(args.dry):
            log.info("This was a dry run. No data written to memory!")
            return True

        if not self.isAddressInSections(args.addr, len(data), sectiontype="RAM"):
            answer = yesno("Warning: Address 0x%08x (len=0x%x) is not inside a RAM section. Continue?" % (args.addr, len(args.data)))
            if not answer:
                return False

        self.progress_log = log.progress("Writing Memory")
        if not self.writeMem(args.addr, data, self.progress_log, bytes_done=0, bytes_total=len(data)):
            self.progress_log.failure("Write failed!")
            return False

        self.progress_log.success("Written %d bytes to 0x%08x." % (len(data), args.addr))

        self.progress_log = log.progress("Launching Command")
        if self.launchRam(args.addr):
            self.progress_log.success("launch_ram cmd was sent successfully!")
            return True
        else:
            self.progress_log.failure("Sending launch_ram command failed!")
            return False

class CmdSendHciCmd(Cmd):
    keywords = ['sendhcicmd']
    description = "Send an arbitrary hci command to the BT controller"
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("cmdcode", type=auto_int,
                        help="The command code (e.g. 0xfc4c for WriteRam).")
    parser.add_argument("data", nargs="*",
                        help="Payload as combinations of hexstrings and hex-uint32 (starting with 0x..)")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        if args.cmdcode > 0xffff:
            log.info("cmdcode needs to be in the range of 0x0000 - 0xffff")
            return False

        data = ''
        for data_part in args.data:
            if data_part[0:2] == "0x":
                data += p32(auto_int(data_part))
            else:
                data += data_part.decode('hex')

        self.internalblue.sendHciCommand(args.cmdcode, data)

        return True

class CmdPatch(Cmd):
    keywords = ['patch']
    description = "Patches 4 byte of data at a specified ROM address."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--hex", action="store_true",
                        help="Interpret data as hex string (e.g. ff000a20...)")
    parser.add_argument("--int", action="store_true",
                        help="Interpret data as 32 bit integer (e.g. 0x123)")
    parser.add_argument("--asm", action="store_true",
                        help="Interpret data as assembler instruction")
    parser.add_argument("--delete", "-d", action="store_true",
                        help="Delete the specified patch.")
    parser.add_argument("--slot", "-s", type=auto_int,
                        help="Patchram slot to use (0-128)") 
    parser.add_argument("--address", "-a", type=auto_int,
                        help="Destination address") 
    parser.add_argument("data", nargs="*",
                        help="Data as string (or hexstring/integer/instruction, see --hex, --int, --asm)")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        if args.slot != None:
            if args.slot < 0 or args.slot > 128:
                log.warn("Slot has to be in the range 0 to 128!")
                return False

        # Patch Deletion
        if args.delete:
            if args.slot != None:
                log.info("Deleting patch in slot %d..." % args.slot)
            elif args.address != None:
                log.info("Deleting patch at address 0x%x..." % args.address)
            else:
                log.warn("Address or Slot number required!")
                return False
            return self.internalblue.disableRomPatch(args.address, args.slot)

        if args.address == None:
            log.warn("Address is required!")
            return False

        if len(args.data) > 0:
            data = ' '.join(args.data)
            if args.hex:
                try:
                    data = data.decode('hex')
                except TypeError as e:
                    log.warn("Data string cannot be converted to hexstring: " + str(e))
                    return False
            elif args.int:
                data = p32(auto_int(data))
            elif args.asm:
                data = asm(data, vma=args.address)
        else:
            self.parser.print_usage()
            print("Data is required!")
            return False

        if len(data) > 4:
            log.warn("Data size is %d bytes. Trunkating to 4 byte!" % len(data))
            data = data[0:4]
        if len(data) < 4:
            log.warn("Data size is %d bytes. 0-Padding to 4 byte!" % len(data))
            data = data.ljust(4, "\x00")

        if args.address != None and not self.isAddressInSections(args.address, len(data), sectiontype="ROM"):
            answer = yesno("Warning: Address 0x%08x (len=0x%x) is not inside a ROM section. Continue?" % (args.address, len(data)))
            if not answer:
                return False

        return self.internalblue.patchRom(args.address, data, args.slot)

# TODO: add a custom arg to directly send autocompletable crafted packet
# TODO: sendlmp -o pause_encryption_req
# TODO: sendlmp -o switch_req
class CmdSendLmp(Cmd):
    keywords = ['sendlmp']
    description = "Send LMP packet to another device."
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("--conn_number", "-n", type=auto_int,
                        help="Number of the connection associated with the other device.") 
    parser.add_argument("--nocheck", action="store_true",
                        help="Do not verify that connection number is valid (fast but unsafe)")
    parser.add_argument("--extended", "-e", action="store_true",
                        help="Use extended opcodes (prepend opcode with 0x7F)")
    parser.add_argument("opcode", type=auto_int,
                        help="Number of the LMP opcode") 
    parser.add_argument("data",
                        help="Payload as hexstring.")

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        connection_number = args.conn_number
        remote_addr = None
        if connection_number == None:
            connection = None
            found_multiple_active = False
            log.info("Reading connection information to find active connection number...")
            for i in range(self.internalblue.fw.CONNECTION_ARRAY_SIZE):
                tmp_connection = self.internalblue.readConnectionInformation(i+1)
                if tmp_connection != None and tmp_connection["remote_address"] != "\x00"*6:
                    log.info("Found active connection with number %d (%s)." %
                            (i+1, bt_addr_to_str(tmp_connection["remote_address"])))
                    if connection != None:
                        found_multiple_active = True
                    connection = tmp_connection

            if connection == None:
                log.warn("No active connection found!")
                return False
            if found_multiple_active:
                log.warn("Multiple active connections detected. Please specify connection number with -n!")
                return False

            connection_number = connection["connection_number"]
            remote_addr = bt_addr_to_str(connection["remote_address"])
        else:
            if args.nocheck:
                remote_addr = "?"
            else:
                connection = self.internalblue.readConnectionInformation(connection_number)
                if connection == None:
                    log.warn("Connection entry at number %d is empty!" % connection_number)
                    return False
                else:
                    remote_addr = bt_addr_to_str(connection["remote_address"])

        data = None
        try:
            data = args.data.decode('hex')
        except TypeError as e:
            log.warn("Data string cannot be converted to hexstring: " + str(e))
            return False

        log.info("Sending op=%d data=%s to connection nr=%d (%s)" %
                (args.opcode, data.encode("hex"), connection_number, remote_addr))
        return self.internalblue.sendLmpPacket(connection_number, args.opcode,
                        data, extended_op=args.extended)


class CmdInfo(Cmd):
    keywords = ['info', 'show', 'i']
    description = "Display various types of information parsed from live RAM"
    parser = argparse.ArgumentParser(prog=keywords[0],
                                     description=description,
                                     epilog="Aliases: " + ", ".join(keywords))
    parser.add_argument("type", 
                        help="Type of information.")

    def infoConnections(self):
        for i in range(self.internalblue.fw.CONNECTION_ARRAY_SIZE):
            connection = self.internalblue.readConnectionInformation(i+1)
            if connection == None:
                continue

            log.info("### | Connection ---%02d--- ###" % i)
            log.info("    - Number:            %d"     % connection["connection_number"])
            log.info("    - Remote BT address: %s"     % bt_addr_to_str(connection["remote_address"]))
            log.info("    - Remote BT name:    %08X"   % connection["remote_name_address"])
            log.info("    - Master of Conn.:   %s"     % str(connection["master_of_connection"]))
            log.info("    - Conn. Handle:      0x%X"   % connection["connection_handle"])
            log.info("    - Public RAND:       %s"     % connection["public_rand"].encode('hex'))
            #log.info("    - PIN:               %s"     % connection["pin"].encode('hex'))
            #log.info("    - BT addr for key:   %s"     % bt_addr_to_str(connection["bt_addr_for_key"]))
            log.info("    - Effective Key Len: %d byte (%d bit)" % (connection["effective_key_len"], 8*connection["effective_key_len"]))
            log.info("    - Link Key:          %s"     % connection["link_key"].encode('hex'))
            log.info("    - LMP Features:      %s"     % connection["extended_lmp_feat"].encode('hex'))
            log.info("    - Host Supported F:  %s"     % connection["host_supported_feat"].encode('hex'))
            log.info("    - TX Power (dBm):    %d"     % connection["tx_pwr_lvl_dBm"])
            log.info("    - Array Index:       %s"     % connection["id"].encode('hex'))
        print

    def infoDevice(self):
        bt_addr      = self.readMem(self.internalblue.fw.BD_ADDR, 6)[::-1]
        bt_addr_str  = ":".join([b.encode("hex") for b in bt_addr])
        device_name  = self.readMem(self.internalblue.fw.DEVICE_NAME, 258)
        device_name_len = u8(device_name[0])-1
        device_name  = device_name[2:2+device_name_len]
        adb_serial   = context.device

        log.info("### | Device ###")
        log.info("    - Name:       %s" % device_name)
        log.info("    - ADB Serial: %s" % adb_serial)
        log.info("    - Address:    %s" % bt_addr_str)

    def infoPatchram(self):
        table_addresses, table_values, table_slots = self.internalblue.getPatchramState()
        log.info("### | Patchram Table ###")
        for i in range(self.internalblue.fw.PATCHRAM_NUMBER_OF_SLOTS):
            if table_slots[i] == 1:
                code = disasm(table_values[i],vma=table_addresses[i],byte=False,offset=False)
                code = code.replace("    ", " ").replace("\n", ";  ")
                log.info("[%03d] 0x%08X: %s (%s)" % (i, table_addresses[i],
                                                 table_values[i].encode('hex'),
                                                 code))

    def work(self):
        args = self.getArgs()
        if args == None:
            return True

        subcommands = {}
        subcommands["connections"] = self.infoConnections
        subcommands["device"] = self.infoDevice
        subcommands["patchram"] = self.infoPatchram

        if args.type in subcommands:
            subcommands[args.type]()
        else:
            log.warn("Unkown type: %s\nKnown types: %s" % (args.type, subcommands.keys()))
            return False
        return True



