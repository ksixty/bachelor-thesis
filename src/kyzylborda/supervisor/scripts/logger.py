#!/usr/bin/env python3

from typing import Dict
import sys
from argparse import ArgumentParser


arg_parser = ArgumentParser(description="Structured logging for supervisord.")


class SupervisorLogger:
    _buffers: Dict[int, str]
    _output: str

    def __init__(self):
        self._buffers = {}
        self._output = ""

    def _write_line(self, pid, process, line):
        full_line = f"{process}[{pid}]: {line}\n"
        self._output = self._output + full_line

    def pop_output(self):
        output = self._output
        self._output = ""
        return output

    def process_log(self, data):
        log_lines = data.split('\n', maxsplit=1)
        log_header_line = log_lines[0]
        log_data = log_lines[1]
        log_headers = dict([ x.split(':') for x in log_header_line.split() ])
        process = log_headers["processname"]
        pid = int(log_headers["pid"])

        if pid in self._buffers:
            log_data = self._buffers[pid] + log_data
            had_buffer = True
        else:
            had_buffer = False

        log_lines = log_data.split('\n')
        for line in log_lines[:-1]:
            self._write_line(pid, process, line)
        last_line = log_lines[-1]

        if len(last_line) == 0:
            if had_buffer:
                del self._buffers[pid]
        else:
            self._buffers[pid] = last_line

    def process_exit(self, data):
        exit_headers = dict([ x.split(':') for x in data.split() ])
        process = exit_headers["processname"]
        pid = int(exit_headers["pid"])

        if pid in self._buffers:
            self._write_line(pid, process, self._buffers[pid])
            del self._buffers[pid]


def main():
    args = arg_parser.parse_args()
    sup_logger = SupervisorLogger()

    while True:
        sys.stdout.buffer.write(b"READY\n")
        sys.stdout.buffer.flush()

        raw_headers = sys.stdin.readline()
        headers = dict([ x.split(':') for x in raw_headers.split() ])
        data_len = int(headers["len"])
        data = sys.stdin.read(data_len)

        if headers["eventname"] == "PROCESS_STATE_EXITED":
            sup_logger.process_exit(data)
        elif headers["eventname"] in ["PROCESS_LOG_STDOUT", "PROCESS_LOG_STDERR"]:
            sup_logger.process_log(data)

        # transition from READY to ACKNOWLEDGED
        output = sup_logger.pop_output().encode("utf-8")
        # length is expected in bytes
        header = f"RESULT {len(output)}\n".encode("utf-8")
        sys.stdout.buffer.write(header + output)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
