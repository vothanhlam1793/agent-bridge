"""Example backend command producer; run on the hub host."""
import argparse
import json
from pathlib import Path
from .server import enqueue_command

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--client', required=True)
    parser.add_argument('--file', required=True, help='UTF-8 JSON command file')
    args = parser.parse_args()
    command = json.loads(Path(args.file).read_text(encoding='utf-8'))
    enqueue_command(args.client, command)
    print('Queued:', command['id'])
