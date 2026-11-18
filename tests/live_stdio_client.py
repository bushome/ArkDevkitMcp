"""Interactive live MCP test client; logs real stdio requests and editor responses.

Run with Python 3.11. Feed JSON objects containing tool/args or method/params.
This utility does not select or modify assets on its own.
"""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def main():
    root = Path(__file__).resolve().parents[1]
    report = Path(sys.argv[1])
    report.parent.mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen([sys.executable, '-u', str(root / 'src/arkmcp_server.py')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', cwd=root)
    responses = queue.Queue()
    stderr = []

    def read_stdout():
        for line in child.stdout:
            try:
                responses.put(json.loads(line))
            except ValueError:
                responses.put({'error': {'message': 'Invalid server stdout: ' + line}})
        responses.put(None)

    def read_stderr():
        for line in child.stderr:
            stderr.append(line.rstrip())

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()
    records = []
    counter = 0

    def rpc(method, params):
        nonlocal counter
        counter += 1
        request = dict(jsonrpc='2.0', id=counter, method=method, params=params)
        start = time.monotonic()
        child.stdin.write(json.dumps(request) + '\n')
        child.stdin.flush()
        response = responses.get(timeout=180)
        if response is None:
            raise RuntimeError('MCP server exited')
        if response.get('id') != counter:
            raise RuntimeError('Unexpected response ID')
        record = dict(request=request, response=response, seconds=round(time.monotonic()-start, 3))
        records.append(record)
        report.write_text(json.dumps(dict(calls=records, stderr=stderr), indent=2), encoding='utf-8')
        result = response.get('result', response)
        if 'content' in result:
            raw = '\n'.join(x.get('text', '') for x in result['content'])
            try:
                data = json.loads(raw)
            except ValueError:
                data = raw
            return dict(id=counter, isError=result.get('isError', False), data=data)
        return dict(id=counter, data=result)

    try:
        print(json.dumps(rpc('initialize', dict(protocolVersion='2025-06-18', capabilities={},
                                               clientInfo=dict(name='arkmcp-live-check', version='1')))), flush=True)
        child.stdin.write(json.dumps(dict(jsonrpc='2.0', method='notifications/initialized')) + '\n')
        child.stdin.flush()
        for line in sys.stdin:
            if not line.strip():
                continue
            command = json.loads(line)
            if 'tool' in command:
                result = rpc('tools/call', dict(name=command['tool'], arguments=command.get('args', {})))
            else:
                result = rpc(command['method'], command.get('params', {}))
            if command.get('summary') and isinstance(result.get('data'), dict):
                data = result['data']
                result['data'] = {k: (len(v) if isinstance(v, list) else v) for k, v in data.items()}
            print(json.dumps(result, ensure_ascii=True), flush=True)
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=10)
        report.write_text(json.dumps(dict(calls=records, stderr=stderr, exit_code=child.returncode), indent=2),
                          encoding='utf-8')


if __name__ == '__main__':
    main()
